(() => {
  'use strict';

  const SOURCE_PATH = /^\/documents\/[A-Za-z0-9_-]+\/source\?snapshot=[A-Za-z0-9_-]+$/;
  const LOOPBACK_HOSTS = ['127.0.0.1', 'localhost', '[::1]'];
  const REQUEST_TIMEOUT = 45_000;
  const DOCUMENT_TIMEOUT = 600_000;
  const STREAM_PROGRESS_TIMEOUT = 120_000;

  function normalizeBaseUrl(value) {
    const raw = String(value ?? '').trim();
    const authority = raw.match(/^https?:\/\/([^/?#\\]+)/i);
    if (!authority || authority[1].includes('@') || /[\u0000-\u0020\u007f\\?#]/.test(raw)) {
      throw new Error('请输入有效的 HTTP 或 HTTPS 后端地址。');
    }
    const url = new URL(raw);
    return url.origin + url.pathname.replace(/\/+$/, '');
  }

  function segment(value) {
    if (typeof value !== 'string' || !/^[A-Za-z0-9_-]+$/.test(value)) {
      throw new Error('知识库资源编号无效。');
    }
    return value;
  }

  function sourcePath(docId, snapshot) {
    return `/documents/${segment(docId)}/source?snapshot=${segment(snapshot)}`;
  }

  function apiError(message, status, kind) {
    return Object.assign(new Error(message), { status, kind });
  }

  async function responseError(response) {
    let detail;
    try { detail = (await response.json())?.detail; }
    catch { /* A gateway can return an HTML error body. */ }
    if (Array.isArray(detail)) {
      detail = detail.map(item => item?.msg).filter(Boolean).join('，');
    }
    return apiError(typeof detail === 'string' && detail.trim()
      ? detail : `请求未完成（HTTP ${response.status}），请检查后重试。`, response.status, 'http');
  }

  function sseParser() {
    const decoder = new TextDecoder();
    let buffer = '';
    let fields = [];
    return chunk => {
      buffer += decoder.decode(chunk, { stream: true });
      const events = [];
      let newline;
      while ((newline = buffer.indexOf('\n')) !== -1) {
        const line = buffer.slice(0, newline).replace(/\r$/, '');
        buffer = buffer.slice(newline + 1);
        if (line === '') {
          if (fields.length) events.push(fields.join('\n'));
          fields = [];
        } else if (line.startsWith('data:')) {
          fields.push(line.slice(5).replace(/^ /, ''));
        } else if (line === 'data') {
          fields.push('');
        }
      }
      return events;
    };
  }

  function createClient({ baseUrl = window.location.origin, token = '', fetchImpl = window.fetch.bind(window) } = {}) {
    const base = normalizeBaseUrl(baseUrl);
    let accessToken = token.trim();
    let bootstrapRequest;

    async function exchange(path, { method = 'GET', headers, body, signal, timeoutMs = REQUEST_TIMEOUT } = {}, consume, authenticate = true) {
      if (authenticate && !accessToken) await bootstrap();
      const controller = new AbortController();
      const abort = () => controller.abort(signal.reason);
      if (signal?.aborted) abort();
      else signal?.addEventListener('abort', abort, { once: true });

      let timer;
      let timedOut = '';
      function setTimeoutFor(duration, message) {
        clearTimeout(timer);
        timer = setTimeout(() => {
          timedOut = message;
          controller.abort();
        }, duration);
      }

      setTimeoutFor(timeoutMs, '等待知识库服务响应超时，请重试。');
      try {
        const requestToken = authenticate ? accessToken : '';
        let response;
        try {
          response = await fetchImpl(base + path, {
            method, body, signal: controller.signal,
            headers: requestToken ? { ...headers, Authorization: `Bearer ${requestToken}` } : headers,
            cache: 'no-store', credentials: 'omit', redirect: 'error',
          });
        } catch (error) {
          if (controller.signal.aborted) throw error;
          throw apiError('无法连接知识库服务，请检查地址和服务状态。', undefined, 'transport');
        }
        if (!response.ok) {
          if (response.status === 401 && requestToken === accessToken) accessToken = '';
          throw await responseError(response);
        }
        return await consume(response, setTimeoutFor);
      } catch (error) {
        if (timedOut) throw apiError(timedOut, undefined, 'timeout');
        throw error;
      } finally {
        clearTimeout(timer);
        signal?.removeEventListener('abort', abort);
      }
    }

    function json(path, options) {
      return exchange(path, options, async response => {
        try { return await response.json(); }
        catch (error) {
          if (error.name === 'AbortError') throw error;
          throw apiError('知识库服务返回了无效的数据。', undefined, 'format');
        }
      });
    }

    async function bootstrap() {
      if (accessToken) return accessToken;
      const url = new URL(base);
      if (base !== window.location.origin || url.protocol !== 'http:' || !LOOPBACK_HOSTS.includes(url.hostname)) {
        throw apiError('请在连接设置中填写访问密钥。', 401, 'auth');
      }
      if (!bootstrapRequest) {
        bootstrapRequest = exchange('/access/token', undefined, response => response.json(), false)
          .then(data => {
            if (typeof data?.access_token !== 'string' || !data.access_token.trim()) {
              throw apiError('服务未提供有效的访问密钥。', undefined, 'auth');
            }
            accessToken ||= data.access_token.trim();
            return accessToken;
          }).finally(() => { bootstrapRequest = undefined; });
      }
      return bootstrapRequest;
    }

    function post(payload, signal) {
      return { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload), signal };
    }

    function stream(path, payload, { onEvent = () => {}, signal } = {}, validateDone) {
      return exchange(path, post(payload, signal), async (response, timeout) => {
        const type = response.headers.get('Content-Type') || '';
        if (!response.body || !/^text\/event-stream\b/i.test(type)) {
          throw apiError('服务未返回有效的回答流。', undefined, 'stream');
        }
        const reader = response.body.getReader();
        const parse = sseParser();
        const steps = new Map();
        const resetProgress = () => timeout(STREAM_PROGRESS_TIMEOUT, '生成进度超时，请检查模型服务后重试。');
        resetProgress();
        try {
          while (true) {
            const { value, done } = await reader.read();
            if (done) throw apiError('连接在回答完成前结束，请重试。', undefined, 'stream');
            for (const frame of parse(value)) {
              let event;
              try { event = JSON.parse(frame); }
              catch { throw apiError('服务返回了无效的回答事件。', undefined, 'stream'); }
              if (!event || typeof event.event !== 'string') {
                throw apiError('服务返回了无效的回答事件。', undefined, 'stream');
              }
              if (event.event === 'error') {
                throw apiError(event.message || event.data || '本次知识库请求未完成。', event.status, 'stream');
              }
              if (event.event === 'done') {
                if (!validateDone(event)) throw apiError('服务返回了无效的完成状态。', undefined, 'stream');
                onEvent(event);
                return event;
              }
              if (event.event === 'step' && typeof event.node === 'string' && steps.get(event.node) !== event.status) {
                steps.set(event.node, event.status);
                resetProgress();
              }
              if (event.event === 'answer_reset' || (event.event === 'delta' && typeof event.text === 'string' && event.text)) {
                resetProgress();
              }
              onEvent(event);
            }
          }
        } finally {
          void reader.cancel().catch(() => {});
          reader.releaseLock();
        }
      });
    }

    return {
      baseUrl: base,
      bootstrap,
      getToken: () => accessToken,
      setToken: value => { accessToken = value.trim(); },
      health: () => json('/health'),
      listDocuments: () => json('/documents'),
      upload: (file, overwrite = false) => {
        const query = new URLSearchParams({ filename: file.name, overwrite });
        return json(`/documents/upload?${query}`, {
          method: 'POST', body: file, headers: { 'Content-Type': 'application/octet-stream' }, timeoutMs: DOCUMENT_TIMEOUT,
        });
      },
      reindex: () => json('/documents/reindex', { method: 'POST', timeoutMs: DOCUMENT_TIMEOUT }),
      deleteDocument: (docId, revisionId) => json(`/documents/${segment(docId)}?${new URLSearchParams({ expected_revision_id: revisionId })}`, {
        method: 'DELETE', timeoutMs: DOCUMENT_TIMEOUT,
      }),
      readSource: (pathOrId, snapshot) => {
        const path = snapshot === undefined ? pathOrId : sourcePath(pathOrId, snapshot);
        if (typeof path !== 'string' || !SOURCE_PATH.test(path)) throw new Error('原文地址无效。');
        return json(path);
      },
      createSession: () => json('/sessions', { method: 'POST' }),
      readSession: id => json(`/sessions/${segment(id)}`),
      cancelSession: id => json(`/sessions/${segment(id)}/cancel`, { method: 'POST' }),
      learningStream: (id, payload, options) => stream(`/sessions/${segment(id)}/actions/stream`, payload, options,
        event => event.session?.session_id === id),
      chatStream: (payload, options) => stream('/chat/stream', payload, options,
        event => typeof (event.answer ?? event.data) === 'string'),
      reviews: (dueOnly = true) => json(`/learning/reviews?${new URLSearchParams({ due_only: dueOnly })}`),
      reviewQuestion: id => json(`/learning/reviews/${segment(id)}/question`),
      answerReview: (id, payload) => json(`/learning/reviews/${segment(id)}/answer`, post(payload)),
      progress: () => json('/learning/progress'),
    };
  }

  window.createKnowledgeApi = createClient;
  window.createKnowledgeApi.normalizeBaseUrl = normalizeBaseUrl;
  window.createKnowledgeApi.sourcePath = sourcePath;
})();
