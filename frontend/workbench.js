(function () {
  'use strict';

  window.createKnowledgeDemo = function createKnowledgeDemo(shell) {
    const liveMode = Boolean(document.querySelector('meta[name="knowledge-backend"]'));
    const STORAGE_KEY = liveMode ? 'knowledge-shelf-live-v1' : 'knowledge-shelf-demo-v1';
    if (liveMode) {
      document.querySelector('.demo-status').textContent = 'LOCAL LIBRARY / 本机知识库';
      document.querySelector('.sheet-foot span:last-child').textContent = '本机知识库';
    }
    const DAY = 86400000;
    const MAX_FILE_BYTES = 500 * 1024 * 1024;
    const escape = value => String(value ?? '').replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[character]));
    const seedNotes = [
      ['valley', '走进向量检索', 'RETRIEVAL / 向量检索', '# 走进向量检索\n\n向量检索先把文本转成 embedding，再用向量之间的相似度寻找语义接近的片段。相似度高说明表示接近，仍需检查原文是否支持问题。\n\n## 从文本到向量\n\n文档通常先切成片段，再为每个片段生成向量。提问时，使用相同的 embedding 模型编码问题。\n\n## 使用边界\n\n专有名词、编号和精确引用可以结合关键词检索。召回内容需要带上文档来源与片段位置。'],
      ['bluehour', 'BM25的关键词世界', 'RETRIEVAL / 关键词检索', '# BM25的关键词世界\n\nBM25 根据查询词在文档中的出现情况进行排序。它综合词频、逆文档频率与文档长度归一化，对精确术语和产品编号通常很有帮助。\n\n## 为什么需要关键词\n\n例如搜索 MCP 或某个错误码时，词面匹配可以找回包含这些词的原文。\n\n## 中文分词\n\n中文的分词方式会影响召回。评估时应包含真实的中文问题和专有名词。'],
      ['river', '文本分块笔记', 'INGESTION / 文本分块', '# 文本分块笔记\n\n分块把较长文档拆成适合检索与放入上下文的片段。可以按标题、段落或长度划分，并保留文档名与标题路径。\n\n## 块大小与重叠\n\n块太小可能丢失语境，块太大可能混入多个主题。重叠可以保留边界附近的信息，也会增加重复内容。\n\n## 评估方法\n\n使用一组真实问题检查目标答案是否落在召回片段中，再调整大小和重叠。'],
      ['summer', 'LangGraph工作流', 'AGENTS / 工作流', '# LangGraph工作流\n\nLangGraph 用节点与边表达有状态工作流。节点读取状态并返回状态更新，边决定接下来执行哪个节点。\n\n## 检索与生成\n\n一个简单的 RAG 图可以包含 retrieve 与 generate 两个节点。导入和建立索引可以独立于这个查询图。\n\n## 状态设计\n\n明确问题、检索结果、回答和错误分别存在哪里。需要恢复执行时，还要检查持久化和重试策略。'],
      ['green', '学习与长期记忆', 'LEARNING / 记忆规划', '# 学习与长期记忆\n\n长期记忆可以记录用户明确表达的偏好、已学概念和需要复习的知识点。设计时要区分事实记录与从一次回答推测出的熟练程度。\n\n## 何时写入\n\n写入需要明确的数据来源、更新规则和删除入口。重复记录应可以合并，矛盾记录应有解决方式。\n\n## 学习证据\n\n独立完成的测验可以作为学习证据。看过讲解和复制示例适合记录为学习经历。'],
      ['noon', '混合检索与RRF重排', 'RAG / HYBRID RETRIEVAL', '# 混合检索与RRF重排\n\n混合检索结合 BM25 的词面匹配与向量检索的语义匹配。两条召回路径可以找到不同的相关片段。\n\n## RRF融合\n\nRRF 使用名次融合列表。常见形式为 score(d) = Σ 1 / (k + rank(d))。同一片段出现在多条列表中时，各项会累加。\n\n## 融合与重排\n\nRRF 得到候选顺序以后，还可以用 CrossEncoder 对问题与候选片段的相关性重新评分。需要用相同查询集评估实际效果。'],
      ['observer', '上下文工程', 'CONTEXT / 上下文', '# 上下文工程\n\n上下文工程关注模型在当前任务中能看到哪些信息，以及这些信息如何组织。系统指令、当前问题、检索片段和工具结果都需要清楚的边界。\n\n## 选择相关信息\n\n把与当前问题相关、可追溯的片段放入上下文。信息的数量和顺序会影响回答。\n\n## 来源与指令\n\n检索文档中的命令应作为待分析内容。业务执行仍需遵循用户当前的请求和系统约束。'],
      ['birds', '提示词的结构', 'PROMPTS / 提示词', '# 提示词的结构\n\n清楚的提示词会说明任务、输入资料、约束和输出要求。对需要引用的回答，要让来源与结论能逐条对应。\n\n## 一个小例子\n\n请根据以下笔记解释 RRF，并在每项结论后给出原文片段编号。若资料不足，请明确指出缺少的信息。\n\n## 迭代\n\n先用固定问题集观察失败类型，再改动相应的提示词部分。记录版本与评估结果。'],
      ['storm', 'MCP工具协议', 'MCP / TOOLS', '# MCP工具协议\n\nMCP 为应用向模型提供工具、资源和提示模板定义通信方式。客户端通过协议发现能力，并按输入结构调用工具。\n\n## 工具发现\n\n客户端可以获取工具名称、说明和输入 schema。模型依据当前任务决定需要调用哪些工具。\n\n## 调用结果\n\n服务端执行具体操作，并返回文本、图片或资源等结果。工具失败和业务结果需要分别处理。'],
      ['tide', 'Agent执行循环', 'AGENTS / TOOL LOOP', '# Agent执行循环\n\n一个带工具的 Agent 循环会读取任务与上下文，决定调用工具或直接回答，接收工具结果后继续判断。\n\n## 停止条件\n\n明确任务完成条件、调用上限和失败处理。工具调用成功后，还需要检查业务结果是否达到目标。\n\n## 状态一致性\n\n可能重试的写入操作需要考虑幂等性。部分失败时，应能够识别已经完成的步骤。'],
      ['war', '评估与召回率', 'EVALUATION / METRICS', '# 评估与召回率\n\n检索评估需要一组问题以及每个问题对应的相关资料。Hit@K 关心前 K 个结果中是否至少出现一个相关结果。\n\n## Hit@5\n\n假设十个问题中有八个问题在前五条检索结果里找到了相关片段，那么这组问题的 Hit@5 为 0.8。\n\n## 其他指标\n\nMRR 关注第一个相关结果的倒数排名。选择指标时，要结合实际使用方式和标注定义。'],
      ['door', '索引更新指南', 'INGESTION / INDEXING', '# 索引更新指南\n\n笔记发生变化后，需要更新对应的索引记录。导入流程通常包括读取、分块、生成向量和写入检索存储。\n\n## 同名替换\n\n同名文件替换前应确认用户意图。新版本发布成功后，旧片段和新片段的状态需要保持一致。\n\n## 重建索引\n\n更换分块或 embedding 策略时，可能需要完整重建。重建后用固定问题集核对来源、召回和回答。']
    ];
    const seedDocuments = seedNotes.map(([id, title, topic, content], index) => ({ id, title, topic, content, artId: id, filename: title + '.md', minutes: index % 3 + 3, indexed: true, updatedAt: Date.now() - index * DAY }));
    const quizzes = {
      noon: { question: 'RRF融合两条检索列表时，主要使用什么信息？', options: ['每个候选在列表中的名次', '直接相加两种模型的原始分数', '文档的创建时间'], correct: 0, explanation: 'RRF按名次计算贡献。同一片段出现在多条列表中时，各项贡献累加。' },
      bluehour: { question: '搜索一个精确错误码时，哪条检索路径通常更直接？', options: ['只按文档长度排序', '使用BM25等关键词检索', '随机选择片段'], correct: 1, explanation: '关键词检索可直接匹配错误码的词面形式。' },
      storm: { question: 'MCP客户端发现工具时，可以获取哪项信息？', options: ['工具的输入schema', '用户电脑上的所有密钥', '模型未来的全部回答'], correct: 0, explanation: '工具发现返回名称、说明和输入schema，调用仍由任务和授权决定。' },
      war: { question: '十个问题中，八个在前五条结果里命中相关片段，Hit@5是多少？', options: ['0.5', '0.8', '5'], correct: 1, explanation: '这组问题中命中的比例是8/10，因此Hit@5为0.8。' }
    };
    const defaultQuiz = doc => ({ question: `阅读《${doc.title}》后，哪一种学习方式最有助于确认自己已经理解？`, options: ['独立解释概念，并根据原文检查', '只浏览标题', '把示例复制一遍就结束'], correct: 0, explanation: '独立解释与基于来源的核对能提供更清晰的学习证据。' });
    const variantQuizzes = {
      noon: { question: '同一片段出现在两条RRF列表中时，应该怎样计算它的融合分数？', options: ['只保留第一条列表的贡献', '把它在两条列表中的名次贡献相加', '使用两条列表的文档总数'], correct: 1, explanation: '每条列表中该片段的1/(k+rank)贡献会累加。' },
      bluehour: { question: 'BM25排序会考虑哪组信息？', options: ['词频、逆文档频率与文档长度', '封面颜色与阅读时间', '文件名长度与字体大小'], correct: 0, explanation: 'BM25综合词频、逆文档频率与文档长度归一化。' },
      storm: { question: '工具调用完成后，Agent还应检查什么？', options: ['直接认定所有业务目标已经完成', '具体工具结果是否达到当前任务目标', '把所有资源自动发送给服务端'], correct: 1, explanation: '协议返回结果以后，客户端仍需根据当前任务解释和核对结果。' },
      war: { question: 'MRR最直接关注哪个位置？', options: ['最后一个结果的位置', '文档封面的位置', '第一个相关结果的排名'], correct: 2, explanation: 'MRR计算每个问题第一个相关结果的倒数排名，再取平均。' }
    };
    function learningQuiz(doc) {
      if ((session.quizVersion || 0) % 2 === 0) return quizzes[doc.id] || defaultQuiz(doc);
      return variantQuizzes[doc.id] || { question: `核对《${doc.title}》相关回答时，哪一步最有助于验证来源？`, options: ['沿引用查看原文，并确认片段支持结论', '只检查答案是否足够长', '只观察模型的语气'], correct: 0, explanation: '引用需要能追溯到原文，并实际支持对应结论。' };
    }
    const defaultReviews = [
      { id: 'review-noon', docId: 'noon', dueAt: Date.now() - DAY, attempts: 2, score: 50 },
      { id: 'review-bluehour', docId: 'bluehour', dueAt: Date.now() - DAY / 2, attempts: 1, score: 100 },
      { id: 'review-storm', docId: 'storm', dueAt: Date.now() - DAY / 4, attempts: 1, score: 0 },
      { id: 'review-war', docId: 'war', dueAt: Date.now() - DAY / 8, attempts: 3, score: 100 },
      { id: 'review-observer', docId: 'observer', dueAt: Date.now() + DAY * 2, attempts: 1, score: 100 }
    ];
    let docs = liveMode ? [] : seedDocuments;
    let reviews = liveMode ? [] : defaultReviews;
    let progress = { noon: [100, 0], bluehour: [100], storm: [0], war: [100, 100, 100], observer: [100] };
    let settings = { api: liveMode ? location.origin : 'http://127.0.0.1:8000' };
    let storageAvailable = true;
    let savedHistory = null;
    const isRecord = value => value !== null && typeof value === 'object' && !Array.isArray(value);
    try {
      const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null');
      if (stored?.version === 1) {
        if (!liveMode && Array.isArray(stored.docs)) {
          const seenIds = new Set();
          docs = stored.docs.filter(doc => {
            if (!isRecord(doc) || typeof doc.id !== 'string' || !doc.id || typeof doc.title !== 'string' || typeof doc.content !== 'string' || seenIds.has(doc.id)) return false;
            seenIds.add(doc.id);
            return true;
          }).map(doc => ({
            id: doc.id, title: doc.title || '未命名笔记', content: doc.content,
            filename: typeof doc.filename === 'string' ? doc.filename : (doc.title || '未命名笔记') + '.md',
            topic: typeof doc.topic === 'string' ? doc.topic : 'MARKDOWN / 我的笔记',
            artId: seedDocuments.some(seed => seed.artId === doc.artId) ? doc.artId : 'noon',
            minutes: Number.isFinite(doc.minutes) && doc.minutes > 0 ? Math.ceil(doc.minutes) : Math.max(1, Math.ceil(doc.content.length / 400)),
            indexed: doc.indexed === true, updatedAt: Number.isFinite(doc.updatedAt) ? doc.updatedAt : Date.now()
          }));
        }
        const knownIds = new Set(docs.map(doc => doc.id));
        if (Array.isArray(stored.reviews)) reviews = stored.reviews.filter(review => isRecord(review) && typeof review.id === 'string' && typeof review.docId === 'string' && knownIds.has(review.docId) && Number.isFinite(review.dueAt)).map(review => ({
          id: review.id, docId: review.docId, dueAt: review.dueAt,
          attempts: Number.isFinite(review.attempts) ? Math.max(0, Math.floor(review.attempts)) : 0,
          score: Number.isFinite(review.score) ? Math.max(0, Math.min(100, review.score)) : 0
        }));
        if (isRecord(stored.progress)) progress = Object.fromEntries(Object.entries(stored.progress).filter(([id, scores]) => knownIds.has(id) && Array.isArray(scores)).map(([id, scores]) => [id, scores.filter(score => Number.isFinite(score) && score >= 0 && score <= 100)]));
        if (!liveMode && typeof stored.settings?.api === 'string') settings.api = stored.settings.api;
        savedHistory = stored;
      }
    } catch { storageAvailable = false; }
    const history = window.createLearningHistory(savedHistory, docs);
    let session = history.active;
    let pane = 'documents';
    let selectedFile = null;
    let importBusy = false;
    let pendingReplace = null;
    let pendingDelete = null;
    let documentMessage = '';
    let reviewMessage = '';
    let showAllReviews = false;
    let expandedReview = null;
    let reviewSelection = null;
    let reviewResult = null;
    const compactLearning = matchMedia('(max-width: 950px)');
    let sourceDocId = null;
    let liveSource = null;
    let sourceRequest = 0;
    let liveSelection = null;
    const learningActions = { answer: '提交答案', exercise: '练一道题', remediate: '针对错题补讲', variant: '再练一道变式题', finish: '结束本轮' };
    const answerBases = { local: '本地资料', model: '模型知识', web: '联网资料', none: '资料不足' };
    let sourceWidth = 340;
    let learningOverlay = '';
    let sourceDrag = null;
    let token = '';
    let tokenVisible = false;
    let connectionMessage = '';
    let backend;
    let connected = false;
    let connectionPending = false;
    let readerRequest = 0;
    let liveReviews = [];
    let liveDueCount = 0;
    let liveReviewQuestion = null;
    let liveReviewRequest = null;
    let liveProgress = null;
    let progressMessage = '';
    const log = [liveMode ? '正在连接本机知识库。' : '本地演示已就绪。'];
    const host = document.getElementById('sheet-content');

    function persist() {
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify({ version: 1, ...(liveMode ? {} : { docs, reviews, progress }), settings, conversations: history.conversations, activeId: history.activeId }));
      } catch {
        storageAvailable = false;
        shell.notify('浏览器存储不可用，当前更改将在本次页面中保留。');
      }
    }
    function activity(message) {
      log.unshift(new Date().toLocaleTimeString('zh-CN', { hour12: false }) + ' ' + message);
      log.splice(15);
    }
    function date(value) { return new Date(value).toLocaleDateString('zh-CN', { month: 'long', day: 'numeric' }); }
    function docById(id) { return docs.find(doc => doc.id === id); }
    function button(action, label, className = '', attributes = '') { return `<button type="button" class="btn ${className}" data-action="${action}" ${attributes}>${label}</button>`; }
    function heading(kicker, title, right = '') { return `<div class="pane-title-row reveal"><div><p class="kicker">${kicker}</p><h1 class="pane-heading">${title}</h1></div>${right}</div>`; }
    function notice(message, id = '') { return message ? `<p ${id ? `id="${id}"` : ''} class="notice" role="status">${escape(message)}</p>` : ''; }
    function markdown(text) {
      return text.split(/\n\s*\n/).map(block => {
        const trimmed = block.trim();
        if (trimmed.startsWith('### ')) return `<h3>${escape(trimmed.slice(4))}</h3>`;
        if (trimmed.startsWith('## ')) return `<h2>${escape(trimmed.slice(3))}</h2>`;
        if (trimmed.startsWith('# ')) return `<h2>${escape(trimmed.slice(2))}</h2>`;
        if (trimmed.startsWith('```')) return `<pre>${escape(trimmed.replace(/^```[^\n]*\n?/, '').replace(/```$/, ''))}</pre>`;
        if (/^[-*] /.test(trimmed)) return '<ul>' + trimmed.split('\n').map(line => `<li>${escape(line.replace(/^[-*] /, ''))}</li>`).join('') + '</ul>';
        return `<p>${escape(trimmed).replace(/\n/g, '<br>')}</p>`;
      }).join('');
    }
    function show(name, html, options = {}) {
      cancelSourceDrag();
      pane = name;
      shell.showPane(name, html, options);
    }
    function paneIsOpen(name) {
      return pane === name && (shell.isPaneOpen?.() ?? true);
    }
    function captureLearning() {
      if (pane !== 'learning') return;
      for (const [id, key] of [['learning-mode', 'mode'], ['teaching-mode', 'teaching'], ['question', 'question']]) {
        const input = document.getElementById(id);
        if (input) session[key] = input.value;
      }
    }
    function chatTitle(chat) {
      const text = chat.turns[0]?.question || chat.question.trim();
      const words = text.replace(/\s+/g, ' ');
      return words.length > 56 ? words.slice(0, 56) + '…' : words;
    }
    function chatHistoryMarkup() {
      const conversations = history.conversations.filter(chat => chat.turns.length || chat.question.trim());
      conversations.sort((a, b) => b.updatedAt - a.updatedAt);
      if (!conversations.length) return '<p class="chat-history-empty">发送第一条消息后，会话会显示在这里。</p>';
      return conversations.map(chat => {
        const title = escape(chatTitle(chat));
        return `<button class="chat-history-item" type="button" data-action="select-chat" data-id="${escape(chat.id)}" aria-current="${chat === session}" title="${title}"><span>${title}</span></button>`;
      }).join('');
    }
    function citationLink(citation, turnIndex, citationIndex) {
      const title = `${citation.index ? '[' + citation.index + '] ' : ''}${citation.title || '引用资料'}`;
      if (citation.source_type === 'external') {
        try {
          const url = new URL(citation.external_url);
          if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) return '';
          return `<a class="source-link" href="${escape(url.href)}" target="_blank" rel="noopener noreferrer"><span>${escape(title)}</span><span>打开网页 ↗</span></a>`;
        } catch { return ''; }
      }
      if (!/^\/documents\/[A-Za-z0-9_-]+\/source\?snapshot=[A-Za-z0-9_-]+$/.test(citation.source_url || '')) return '';
      return `<button class="source-link" type="button" data-action="live-citation" data-turn="${turnIndex}" data-citation="${citationIndex}"><span>${escape(title)}</span><span>查看原文 ↗</span></button>`;
    }
    function liveTurnMarkup(turn, turnIndex, current) {
      const answer = turn.answer;
      const basis = answerBases[answer?.basis];
      const citations = answer?.citations.map((citation, index) => citationLink(citation, turnIndex, index)).filter(Boolean).join('');
      const parts = answer?.parts.map(markdown).join('') || '';
      const draft = escape(turn.draft || '');
      const status = turn.busy ? '<p class="chat-waiting" role="status">正在处理…</p>' : notice(turn.error);
      const canRetry = current && turn.request && !turn.busy ? button('retry-learning', '重试上次操作', 'primary') : '';
      const pending = answer?.pending;
      const quiz = answer?.quiz;
      const selection = pending?.kind === 'answer' ? liveSelection : answer?.grading?.user_answer;
      const quizHtml = current && quiz ? `<div class="quiz-panel"><p class="kicker">练习</p><h3>${escape(quiz.question)}</h3><div class="quiz-options">${Object.entries(quiz.options).map(([label, value]) => `<label class="quiz-option"><input type="radio" name="live-quiz-answer" value="${escape(label)}" ${selection === label ? 'checked' : ''} ${pending?.kind !== 'answer' || turn.request ? 'disabled' : ''}><span>${escape(label)}. ${escape(value)}</span></label>`).join('')}</div></div>` : '';
      const actions = current && !turn.request ? pending?.actions.filter(action => Object.hasOwn(learningActions, action)).map(action => button('live-learning-' + action, learningActions[action], action === 'answer' ? 'primary' : '')).join('') : '';
      return `<article class="chat-turn">
        <div class="chat-question">${escape(turn.question)}</div>
        <div class="chat-answer"><p class="kicker">知识库回答${typeof basis === 'string' ? ' · ' + escape(basis) : ''}</p>
          <div class="answer-body">${parts}</div>${status}
          ${current && turn.busy ? `<div class="answer-body" id="live-answer-draft" style="white-space: pre-wrap">${draft}</div>` : ''}
          ${citations ? `<div class="citation-list" aria-label="引用原文">${citations}</div>` : ''}
          ${quizHtml}${actions || canRetry ? `<div class="row learning-actions">${actions || canRetry}</div>` : ''}
        </div>
      </article>`;
    }
    function chatTurnMarkup(turn, current, learningControls) {
      const answer = turn.answer;
      let response = '<p class="chat-waiting" role="status">正在检索演示笔记…</p>';
      if (answer) {
        const sources = answer.docIds.map(docById).filter(Boolean);
        const citations = sources.length ? `<div class="citation-list" aria-label="引用原文">${sources.map(doc => `<button class="source-link" type="button" data-action="citation" data-id="${escape(doc.id)}"><span>${escape(doc.title)}</span><span>查看原文 ↗</span></button>`).join('')}</div>` : '';
        response = `<div class="answer-body"${current ? ' id="answer"' : ''}>${answer.html}</div>${citations}`;
      }
      return `<article class="chat-turn">
        <div class="chat-question">${escape(turn.question)}</div>
        <div class="chat-answer"><p class="kicker">知识库回答</p>${response}${current ? learningControls : ''}</div>
      </article>`;
    }
    function chatSourceMarkup(doc) {
      if (liveMode && liveSource?.message) return notice(liveSource.message);
      if (!doc) return '<div class="chat-source-empty"><p>点击回答中的「查看原文」，可在这里对照阅读。</p></div>';
      return `<article class="reader-content"><h3>${escape(doc.title)}</h3><p class="doc-meta">${escape(doc.filename)} · ${doc.content.length.toLocaleString()} 字</p>${markdown(doc.content.replace(/^#\s+[^\n]+\n*/, ''))}</article>`;
    }
    function renderLearning({ scrollToEnd = false } = {}) {
      const editor = document.activeElement?.id === 'question' ? document.activeElement : null;
      const transcript = host.querySelector('.chat-transcript');
      const transcriptTop = transcript?.scrollTop;
      const wasAtEnd = !transcript || transcript.scrollHeight - transcript.clientHeight - transcriptTop < 32;
      const sourceTop = host.querySelector('.source-scroll')?.scrollTop || 0;
      const historyTop = host.querySelector('.chat-history-list')?.scrollTop || 0;
      const lastTurn = session.turns.at(-1);
      const busy = liveMode ? Boolean(lastTurn?.busy) : lastTurn?.answer === null;
      const pending = liveMode && lastTurn?.answer?.pending;
      const sendDisabled = busy || (liveMode && Boolean(pending || lastTurn?.request));
      const response = lastTurn?.answer;
      const source = liveMode ? liveSource?.doc : docById(sourceDocId);
      const quizDoc = docById(session.quizDocId);
      const quiz = quizDoc ? learningQuiz(quizDoc) : null;
      const options = [['', '自动判断'], ['teach', '教学'], ['assess', '测验'], ['direct', '交流']].map(([value, label]) => `<option value="${value}" ${session.mode === value ? 'selected' : ''}>${label}</option>`).join('');
      const quizHtml = quiz ? `<div class="quiz-panel"><p class="kicker">练习</p><h3>${escape(quiz.question)}</h3><div class="quiz-options">${quiz.options.map((label, i) => `<label class="quiz-option"><input type="radio" name="quiz-answer" value="${i}" ${session.selection === i ? 'checked' : ''} ${session.quizResult !== null ? 'disabled' : ''}><span>${escape(label)}</span></label>`).join('')}</div>${session.quizResult !== null ? `<p class="notice">${session.quizResult === 100 ? '回答正确 · 100 分' : '本次得分 · 0 分'}。${escape(quiz.explanation)}</p>` : button('submit-quiz', '提交答案', 'primary')}</div>` : '';
      const actions = response && !session.finished ? `<div class="row learning-actions">${button('exercise', '练一道题')}${session.quizResult === 0 ? button('remediate', '针对错题补讲') : ''}${session.quizResult !== null ? button('variant', '再练一道变式题') : ''}${button('finish', '结束本轮', 'link')}</div>` : '';
      const title = escape(session.turns.length ? chatTitle(session) : '问答与学习');
      const examplesMarkup = `<div class="chat-examples" aria-label="示例问题">${button('example-hybrid', '混合检索的作用')}${button('example-index', '更新笔记与索引')}${button('example-memory', '长期记忆规划')}</div>`;
      const messages = session.turns.map((turn, index) => liveMode ? liveTurnMarkup(turn, index, turn === lastTurn) : chatTurnMarkup(turn, turn === lastTurn, quizHtml + actions)).join('');
      show('learning', `
        <div class="learning-workspace">
          <aside class="chat-history" id="chat-history" aria-labelledby="chat-history-title">
            <div class="chat-history-head"><h2 id="chat-history-title">历史会话</h2>
              ${button('close-overlay', '关闭', 'chat-history-close chat-mobile-only small', 'id="close-chat-history"')}</div>
            ${button('new-session', '＋ 新建会话', 'chat-new primary', 'id="new-chat"')}
            <nav class="chat-history-list" aria-label="历史聊天记录">${chatHistoryMarkup()}</nav>
          </aside>
          <section class="chat-main" aria-labelledby="chat-title">
            <header class="chat-head"><h1 class="chat-title" id="chat-title" title="${title}">${title}</h1>
              <div class="chat-mobile-actions">
                ${button('open-history', '会话', 'chat-mobile-only small', 'id="show-chat-history" aria-controls="chat-history" aria-expanded="false"')}
                ${button('open-source', '原文', 'chat-mobile-only small', 'id="show-chat-source" aria-controls="chat-source" aria-expanded="false"')}
              </div>
            </header>
            <div class="chat-transcript" role="region" aria-label="对话消息" tabindex="0">
              ${messages || `<div class="chat-empty"><h2>想学点什么？</h2>${examplesMarkup}</div>`}
            </div>
            <form id="learning-form" class="chat-composer">
              <label class="sr-only" for="question">你的问题</label>
              <textarea id="question" class="chat-input" maxlength="4000" rows="2" placeholder="输入问题，查找笔记中的答案">${escape(session.question)}</textarea>
              <div class="chat-composer-controls">
                <div class="chat-modes">
                  <label class="chat-mode" for="learning-mode"><span>学习模式</span><select id="learning-mode">${options}</select></label>
                  <label class="chat-mode" for="teaching-mode"><span>教学方式</span><select id="teaching-mode"><option value="explain" ${session.teaching === 'explain' ? 'selected' : ''}>讲解</option><option value="solve_step" ${session.teaching === 'solve_step' ? 'selected' : ''}>分步指导</option></select></label>
                </div>
                <button class="btn primary chat-send" type="submit" aria-label="提交问题" ${sendDisabled ? 'disabled' : ''}>${busy ? '整理中…' : '发送 ↗'}</button>
              </div>
              <p class="chat-keyboard">${liveMode ? (pending ? '请完成当前学习步骤' : '本机知识库') : '本地演示'} <span aria-hidden="true">·</span> Ctrl / ⌘ + Enter 发送</p>
            </form>
          </section>
          <aside class="chat-source" id="chat-source" aria-labelledby="chat-source-title">
            <div class="source-resizer" id="source-resizer" role="separator" aria-orientation="vertical" aria-label="调整原文栏宽度" aria-controls="chat-source" title="拖动调整原文栏宽度" tabindex="0"></div>
            <div class="chat-source-head"><h2 id="chat-source-title" tabindex="-1">引用原文</h2>
              ${button('close-source', '关闭', 'chat-source-close small', 'id="close-chat-source"')}</div>
            <div class="source-scroll" role="region" aria-label="引用文档内容" tabindex="0">${chatSourceMarkup(source)}</div>
          </aside>
        </div>`, { layout: 'learning', title: '问答与学习' });
      const workspace = host.querySelector('.learning-workspace');
      updateLearningPanels(workspace);
      applySourceWidth(workspace);
      const newTranscript = workspace.querySelector('.chat-transcript');
      newTranscript.scrollTop = scrollToEnd || wasAtEnd ? newTranscript.scrollHeight : transcriptTop;
      workspace.querySelector('.source-scroll').scrollTop = sourceTop;
      workspace.querySelector('.chat-history-list').scrollTop = historyTop;
      if (editor && document.activeElement?.id === 'question') {
        document.activeElement.setSelectionRange(editor.selectionStart, editor.selectionEnd, editor.selectionDirection);
      }
    }
    function updateLearningPanels(workspace) {
      const overlay = compactLearning.matches ? learningOverlay : '';
      workspace.dataset.overlay = overlay;
      workspace.querySelector('.chat-main').inert = Boolean(overlay);
      workspace.querySelector('.chat-history').inert = compactLearning.matches && overlay !== 'history';
      workspace.querySelector('.chat-source').inert = compactLearning.matches && overlay !== 'source';
      workspace.querySelector('#show-chat-history').setAttribute('aria-expanded', String(overlay === 'history'));
      workspace.querySelector('#show-chat-source').setAttribute('aria-expanded', String(overlay === 'source'));
      workspace.querySelector('#close-chat-source').hidden = !compactLearning.matches && !(liveMode ? liveSource : docById(sourceDocId));
    }
    function toggleLearningOverlay(name) {
      const workspace = host.querySelector('.learning-workspace');
      if (!workspace || !compactLearning.matches) return;
      const previous = learningOverlay;
      learningOverlay = name;
      updateLearningPanels(workspace);
      if (name) workspace.querySelector(`#close-chat-${name}`)?.focus({ preventScroll: true });
      else if (previous) workspace.querySelector(`#show-chat-${previous}`)?.focus({ preventScroll: true });
    }
    function openLearningSource(id) {
      const doc = docById(id);
      if (!doc) return;
      sourceDocId = id;
      const workspace = host.querySelector('.learning-workspace');
      const reader = workspace.querySelector('.source-scroll');
      reader.innerHTML = chatSourceMarkup(doc);
      reader.scrollTop = 0;
      if (compactLearning.matches) toggleLearningOverlay('source');
      else {
        updateLearningPanels(workspace);
        workspace.querySelector('#chat-source-title').focus({ preventScroll: true });
      }
    }
    function clearLearningSource() {
      sourceDocId = null;
      liveSource = null;
      sourceRequest++;
    }
    async function openLiveCitation(turnIndex, citationIndex) {
      const citation = session.turns[turnIndex]?.answer?.citations?.[citationIndex];
      if (!citation || !backend) return;
      const request = ++sourceRequest;
      const update = (reset = false) => {
        const workspace = host.querySelector('.learning-workspace');
        if (!workspace || !paneIsOpen('learning')) return;
        const reader = workspace.querySelector('.source-scroll');
        reader.innerHTML = chatSourceMarkup(liveSource?.doc);
        if (reset) reader.scrollTop = 0;
        updateLearningPanels(workspace);
        if (reset && compactLearning.matches) toggleLearningOverlay('source');
        else if (reset) workspace.querySelector('#chat-source-title')?.focus({ preventScroll: true });
      };
      liveSource = { message: '正在读取引用原文…' };
      update(true);
      try {
        const doc = await backend.readSource(citation.source_url);
        if (request !== sourceRequest) return;
        if (typeof doc?.content !== 'string') throw new Error('服务返回了无效的原文。');
        liveSource = { doc: { title: doc.title, filename: doc.relative_path, content: doc.content } };
      } catch (error) {
        if (request !== sourceRequest) return;
        liveSource = { message: error.message };
      }
      update();
    }
    function closeLearningSource() {
      if (compactLearning.matches) { toggleLearningOverlay(''); return; }
      clearLearningSource();
      const workspace = host.querySelector('.learning-workspace');
      workspace.querySelector('.source-scroll').innerHTML = chatSourceMarkup(null);
      updateLearningPanels(workspace);
      document.getElementById('question').focus({ preventScroll: true });
    }
    function sourceWidthLimit(workspace) {
      const historyWidth = workspace.querySelector('.chat-history').offsetWidth;
      return Math.max(270, Math.min(580, workspace.clientWidth - historyWidth - 350));
    }
    function applySourceWidth(workspace, candidate = sourceWidth, maximum = sourceWidthLimit(workspace)) {
      const width = Math.round(Math.max(270, Math.min(candidate, maximum)));
      workspace.style.setProperty('--source-width', `${width}px`);
      const separator = workspace.querySelector('.source-resizer');
      separator.setAttribute('aria-valuemin', '270');
      separator.setAttribute('aria-valuemax', String(maximum));
      separator.setAttribute('aria-valuenow', String(width));
      separator.setAttribute('aria-valuetext', `${width} px`);
      return width;
    }
    function relevantDocs(question) {
      const named = docs.filter(doc => question.toLowerCase().includes(doc.title.toLowerCase()));
      if (named.length) return named.slice(0, 3);
      const groups = [[/rrf|混合检索|融合|重排/i, ['noon', 'bluehour', 'valley']], [/索引|更新|导入|修改笔记/i, ['door', 'river']], [/记忆|学习记录/i, ['green']], [/mcp|协议|工具发现/i, ['storm', 'tide']], [/langgraph|工作流|节点/i, ['summer']], [/hit|mrr|评估|召回率/i, ['war']], [/提示词/i, ['birds']], [/上下文/i, ['observer']], [/向量|embedding/i, ['valley', 'noon']], [/bm25|关键词/i, ['bluehour', 'noon']], [/分块/i, ['river']]];
      const preferred = groups.find(([pattern]) => pattern.test(question))?.[1] || [];
      const matches = preferred.map(id => docById(id)).filter(Boolean);
      if (matches.length) return matches;
      const words = question.toLowerCase().match(/[a-z][a-z0-9]+|[\u4e00-\u9fff]{2,}/g) || [];
      const tokens = [...new Set(words.flatMap(word => /^[a-z]/.test(word) ? [word] : [...word].slice(0, -1).map((_, index) => word.slice(index, index + 2))))].filter(word => !['请根', '根据', '笔记', '讲解', '什么', '怎样', '如何', '一个', '的作', '作用'].includes(word));
      const ranked = docs.map(doc => ({ doc, score: tokens.reduce((sum, tokenValue) => sum + (doc.title.toLowerCase().includes(tokenValue) ? 3 : doc.content.toLowerCase().includes(tokenValue) ? 1 : 0), 0) })).sort((a, b) => b.score - a.score);
      return ranked.filter(item => item.score > 0).slice(0, 3).map(item => item.doc);
    }
    function answerFor(question, teaching) {
      const selected = relevantDocs(question);
      let html;
      if (!selected.length) html = '<p>当前演示笔记中没有找到足够相关的资料。</p><p>你可以先导入一份 Markdown 笔记，再围绕其中的明确术语提问。</p>';
      else if (/混合检索|rrf|融合/i.test(question) && selected.some(doc => /RRF/.test(doc.content))) html = '<p>BM25擅长根据关键词、专有名词与编号找到词面匹配。向量检索通过embedding查找语义接近的片段。两条路径结合，可以补充彼此的召回结果。</p><p>RRF根据候选在各列表中的名次进行融合，常见形式为 <code>Σ 1 / (k + rank)</code>。融合后的候选还可以交给CrossEncoder重新评分。</p>';
      else if (/索引|更新|修改笔记/i.test(question) && selected.some(doc => /重建索引/.test(doc.content))) html = '<p>修改笔记后，重新读取并分块，为新片段生成向量，再更新关键词和向量索引。保留文档来源可以继续从回答追溯原文。</p><p>同名笔记替换前先确认。更换分块或embedding策略时，可重建索引，再用固定问题集检查召回与引用是否正确。</p>';
      else if (/长期记忆|记忆规划/i.test(question) && selected.some(doc => /长期记忆/.test(doc.content))) html = '<p>示例笔记把长期记忆的设计分成三部分，包括明确的记录来源、更新与合并规则，以及可见的删除入口。</p><p>学习进度应根据独立答题等证据更新。具体接入方案还需要确定存储结构、写入时机和数据保留方式。</p>';
      else html = selected.slice(0, 2).map(doc => `<p>${escape(doc.content.split(/\n\s*\n/).filter(block => !block.startsWith('#'))[0] || doc.content)}</p>`).join('');
      if (teaching === 'solve_step' && selected.length) html += '<ol><li>先用自己的话解释这个概念。</li><li>沿下方引用核对原文。</li><li>完成一道练习，检查理解。</li></ol>';
      return { html, docIds: selected.map(doc => doc.id) };
    }
    function requestId() {
      return crypto.randomUUID();
    }
    async function runLiveLearning(conversation, turn, request) {
      if (turn.busy) return;
      if (!backend) { shell.notify('知识库后端尚未连接，请稍后重试。'); return; }
      turn.request = request;
      turn.busy = true;
      turn.error = '';
      turn.draft = '';
      const visible = () => session === conversation && paneIsOpen('learning');
      conversation.updatedAt = Date.now();
      persist();
      if (visible()) renderLearning({ scrollToEnd: true });
      try {
        if (!conversation.backendId) {
          const created = await backend.createSession();
          conversation.backendId = created.session_id;
          persist();
        }
        const result = await backend.learningStream(conversation.backendId, request, { onEvent(event) {
          if (event.event === 'delta' && typeof event.text === 'string') turn.draft += event.text;
          else if (event.event === 'answer_reset') turn.draft = '';
          else return;
          if (!visible()) return;
          const draft = host.querySelector('#live-answer-draft');
          const transcript = host.querySelector('.chat-transcript');
          if (!draft || !transcript) return;
          const follow = transcript.scrollHeight - transcript.clientHeight - transcript.scrollTop < 32;
          if (event.event === 'answer_reset') draft.textContent = '';
          else draft.append(event.text);
          if (follow) transcript.scrollTop = transcript.scrollHeight;
        } });
        const view = result.session;
        const parts = turn.answer?.parts || [];
        const text = typeof view.answer === 'string' ? view.answer : '';
        if (text && parts.at(-1) !== text) parts.push(text);
        turn.answer = {
          parts,
          basis: view.answer_basis || '',
          citations: Array.isArray(view.citations) ? view.citations : [],
          quiz: view.quiz,
          grading: view.grading,
          pending: view.pending
        };
        turn.request = null;
        if (session === conversation) liveSelection = null;
        if (view.grading) void refreshDueCount();
        activity('学习步骤已完成。');
      } catch (error) {
        turn.error = error.message;
        if (error.status === 404 && request.action === 'start') conversation.backendId = null;
      } finally {
        delete turn.busy;
        delete turn.draft;
        persist();
        if (visible()) renderLearning({ scrollToEnd: true });
      }
    }
    function askLive() {
      const latest = session.turns.at(-1);
      if (latest?.request || latest?.answer?.pending) return;
      if (!backend) { shell.notify('知识库后端尚未连接，请稍后重试。'); return; }
      const turn = { question: session.question.trim(), answer: null };
      const request = { request_id: requestId(), action: 'start', message: turn.question, selected_mode: session.mode || null, submode: session.teaching };
      session.turns.push(turn);
      session.question = '';
      void runLiveLearning(session, turn, request);
      document.getElementById('question')?.focus({ preventScroll: true });
    }
    function resumeLiveLearning(action) {
      const turn = session.turns.at(-1);
      if (!turn || turn.busy) return;
      if (action === 'retry') {
        if (turn.request) void runLiveLearning(session, turn, turn.request);
        return;
      }
      const pending = turn.answer?.pending;
      if (turn.request || !pending?.actions.includes(action)) return;
      const request = { request_id: requestId(), action, pending_id: pending.pending_id };
      if (action === 'answer') {
        if (!liveSelection) { shell.notify('请先选择一个答案。'); return; }
        request.answer = liveSelection;
      }
      void runLiveLearning(session, turn, request);
    }
    function ask() {
      captureLearning();
      if (!session.question.trim()) { shell.notify('请先填写你的问题。'); document.getElementById('question')?.focus(); return; }
      if (liveMode) { askLive(); return; }
      if (session.turns.at(-1)?.answer === null) return;
      if (!docs.length) { shell.notify('请先在文档页导入一份笔记。'); return; }
      const conversation = session;
      const { mode, teaching } = conversation;
      const turn = { question: conversation.question.trim(), answer: null };
      conversation.turns.push(turn);
      conversation.updatedAt = Date.now();
      Object.assign(conversation, { question: '', quizDocId: null, quizVersion: 0, quizResult: null, selection: null, finished: false });
      persist();
      renderLearning({ scrollToEnd: true });
      document.getElementById('question')?.focus({ preventScroll: true });
      setTimeout(() => {
        turn.answer = answerFor(turn.question, teaching);
        if (mode === 'assess') conversation.quizDocId = turn.answer.docIds[0] || docs[0]?.id || null;
        activity('已完成一次本地示例问答。');
        persist();
        if (session === conversation && paneIsOpen('learning')) renderLearning();
      }, 850);
    }
    function newSession(draft = '') {
      captureLearning();
      session = history.newChat();
      session.question = draft;
      clearLearningSource();
      liveSelection = null;
      learningOverlay = '';
      persist();
      renderLearning({ scrollToEnd: true });
      document.getElementById('question')?.focus({ preventScroll: true });
    }
    function selectChat(id) {
      captureLearning();
      if (!history.select(id)) return;
      if (session !== history.active) { clearLearningSource(); liveSelection = null; }
      session = history.active;
      learningOverlay = '';
      persist();
      renderLearning({ scrollToEnd: true });
      if (compactLearning.matches) document.getElementById('show-chat-history')?.focus({ preventScroll: true });
      else [...host.querySelectorAll('.chat-history-item')].find(item => item.dataset.id === id)?.focus({ preventScroll: true });
    }
    function exercise(variant = false) {
      captureLearning();
      const previous = session.quizDocId;
      const ids = session.turns.at(-1)?.answer?.docIds.filter(id => docById(id)) || docs.map(doc => doc.id);
      if (!ids.length) { shell.notify('请先导入一份笔记。'); return; }
      session.quizDocId = variant && docById(previous) ? previous : ids[0];
      session.quizVersion = variant ? (session.quizVersion || 0) + 1 : 0;
      session.selection = null;
      session.quizResult = null;
      session.finished = false;
      renderLearning({ scrollToEnd: true });
    }
    function recordAnswer(doc, score) {
      const scores = Array.isArray(progress[doc.id]) ? progress[doc.id] : [];
      progress[doc.id] = [...scores, score];
      let review = reviews.find(item => item.docId === doc.id);
      if (!review) { review = { id: 'review-' + doc.id, docId: doc.id, attempts: 0, score: 0 }; reviews.push(review); }
      review.attempts += 1;
      review.score = score;
      review.dueAt = Date.now() + DAY * (score === 100 ? 3 : 1);
      persist();
      activity(`已记录《${doc.title}》的演示答题结果。`);
      shell.refreshCounter();
    }
    function submitQuiz() {
      const selected = host.querySelector('input[name="quiz-answer"]:checked');
      if (!selected) { shell.notify('请先选择一个答案。'); return; }
      const doc = docById(session.quizDocId);
      if (!doc || session.quizResult !== null) return;
      const quiz = learningQuiz(doc);
      session.selection = Number(selected.value);
      session.quizResult = session.selection === quiz.correct ? 100 : 0;
      recordAnswer(doc, session.quizResult);
      renderLearning({ scrollToEnd: true });
    }
    function showDocumentStatus() {
      if (paneIsOpen('documents')) renderDocuments();
    }
    async function refreshLiveDocuments(announce = false) {
      const result = await backend.listDocuments();
      docs = result.documents.map((doc, index) => ({
        id: doc.doc_id, title: doc.title, filename: doc.relative_path,
        topic: 'MARKDOWN / 我的笔记', content: '', indexed: doc.indexed,
        snapshot: doc.indexed ? result.index?.snapshot_id : null,
        revisionId: doc.revision_id, sizeBytes: doc.size_bytes, parseError: doc.parse_error,
        artId: seedDocuments[index % seedDocuments.length].artId,
        minutes: Math.max(1, Math.ceil(doc.size_bytes / 1200))
      }));
      shell.documentsChanged(docs);
      if (announce) documentMessage = `已刷新 ${docs.length} 份后端文档。`;
      showDocumentStatus();
    }
    async function refreshDocumentsFromServer() {
      try { await refreshLiveDocuments(true); }
      catch (error) { documentMessage = error.message; showDocumentStatus(); }
    }
    async function mutateLiveDocuments(operation, success, onConflict) {
      if (!connected || importBusy) return;
      importBusy = true;
      documentMessage = '后端正在处理文档索引…';
      showDocumentStatus();
      try {
        await operation();
        pendingReplace = null;
        pendingDelete = null;
        documentMessage = success;
        activity(success);
        try { await refreshLiveDocuments(); }
        catch (error) { documentMessage += ' 目录刷新失败：' + error.message; }
        return true;
      } catch (error) {
        if (!onConflict?.(error)) documentMessage = error.message;
        return false;
      } finally {
        importBusy = false;
        showDocumentStatus();
        if (!paneIsOpen('documents')) shell.notify(documentMessage);
      }
    }
    async function uploadLiveDocument(file, overwrite = false) {
      const completed = await mutateLiveDocuments(
        () => backend.upload(file, overwrite),
        `《${file.name}》已${overwrite ? '替换' : '导入'}并建立索引。`,
        error => {
          if (overwrite || error.status !== 409 || !error.message.includes('同名文档')) return false;
          pendingReplace = { file, existing: { title: file.name } };
          pendingDelete = null;
          documentMessage = '';
          return true;
        }
      );
      if (completed) { selectedFile = null; showDocumentStatus(); }
    }
    function renderDocuments() {
      const controlsDisabled = importBusy || (liveMode && !connected);
      const indexed = docs.filter(doc => doc.indexed).length;
      const confirm = pendingReplace ? `<div class="notice confirmation" role="alert"><p>同名文档已存在，确认用新内容替换《${escape(pendingReplace.existing.title)}》？</p><div class="row">${button('confirm-replace', '确认替换', 'primary', controlsDisabled ? 'disabled' : '')}${button('cancel-replace', '取消')}</div></div>` : pendingDelete ? `<div class="notice confirmation" role="alert"><p>确认删除《${escape(docById(pendingDelete)?.title)}》${liveMode ? '' : '及其演示学习记录'}？</p><div class="row">${button('confirm-delete', '确认删除', 'primary', controlsDisabled ? 'disabled' : '')}${button('cancel-delete', '取消')}</div></div>` : '';
      show('documents', `${heading('ARCHIVE / 文档', '我的文档', button('refresh-documents', '刷新', '', controlsDisabled ? 'disabled' : ''))}<div class="pane-grid document-grid">
        <section class="panel reveal"><div class="panel-head"><h2>导入 Markdown 笔记</h2><span class="muted">.MD</span></div><label class="file-drop field" for="file"><span class="upload-symbol">＋</span><strong>${selectedFile ? escape(selectedFile.name) : '选择一份 Markdown 笔记'}</strong><input id="file" type="file" accept=".md,text/markdown"><span class="form-hint">支持 UTF-8 编码的 .md 文件，每份上限 500 MiB。</span></label>${button('upload', importBusy ? '正在处理…' : '导入并建立索引', 'primary', controlsDisabled ? 'disabled' : '')}<p class="form-hint">${liveMode ? '文件由本机后端保存并建立索引。' : '文件保存在当前浏览器，索引过程用于本地演示。'}</p>${confirm}${notice(documentMessage || (liveMode && !connected ? connectionMessage || '正在连接后端…' : ''))}${!storageAvailable ? notice('浏览器存储不可用，本次操作仅保留到页面关闭。') : ''}</section>
        <section class="panel reveal"><div class="panel-head"><h2>文档目录 <span class="muted">${docs.length}</span></h2>${button('reindex', '重建索引', 'small', controlsDisabled ? 'disabled' : '')}</div><p class="form-hint">${docs.length} 份文档 · ${indexed} 份已建立${liveMode ? '' : '演示'}索引</p><div class="doc-list">${docs.length ? docs.map((doc, i) => `<article class="doc-row"><span class="doc-number">${String(i + 1).padStart(2, '0')}</span><div class="doc-info"><button type="button" class="doc-title" data-action="read" data-id="${escape(doc.id)}">${escape(doc.title)}</button><p class="doc-meta">${escape(doc.topic)} · ${liveMode ? doc.sizeBytes.toLocaleString() + ' 字节' : doc.content.length.toLocaleString() + ' 字'} · ${doc.indexed ? '已索引' : '待索引'}</p></div><div class="row">${button('read', '原文', 'small', `data-id="${escape(doc.id)}" ${liveMode && !doc.snapshot ? 'disabled' : ''}`)}${button('delete', '删除', 'small link', `data-id="${escape(doc.id)}" ${controlsDisabled ? 'disabled' : ''}`)}</div></article>`).join('') : '<div class="empty-state"><h3>书架等待新的笔记。</h3><p>导入第一份 Markdown 文档开始体验。</p></div>'}</div></section></div>`, { title: '我的文档' });
    }
    function commitImport(data, existing = null) {
      const doc = { id: existing?.id || 'note-' + Date.now().toString(36), title: data.title, filename: data.filename, content: data.content, topic: 'MARKDOWN / 我的笔记', artId: existing?.artId || seedDocuments[docs.length % seedDocuments.length].artId, minutes: Math.max(1, Math.ceil(data.content.length / 400)), indexed: true, updatedAt: Date.now() };
      docs = existing ? docs.map(item => item.id === existing.id ? doc : item) : [...docs, doc];
      pendingReplace = null;
      selectedFile = null;
      documentMessage = `《${doc.title}》已${existing ? '替换' : '导入'}，演示索引已更新。`;
      activity(documentMessage);
      persist();
      shell.documentsChanged(docs);
      shell.selectDocument(doc.id);
      if (paneIsOpen('documents')) renderDocuments();
      else shell.notify(documentMessage);
    }
    async function importDocument() {
      if (importBusy) return;
      const file = selectedFile;
      if (!file) { shell.notify('请先选择一个 Markdown 文件。'); return; }
      if (!/\.md$/i.test(file.name)) { documentMessage = '请选择 .md 格式的 Markdown 文件。'; renderDocuments(); return; }
      if (file.size > MAX_FILE_BYTES) { documentMessage = '文件超过 500 MiB，请选择更小的文件。'; renderDocuments(); return; }
      if (liveMode) { await uploadLiveDocument(file); return; }
      importBusy = true;
      renderDocuments();
      try {
        const buffer = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = () => reject(new Error('无法读取文件，请重新选择。')); reader.readAsArrayBuffer(file); });
        const content = new TextDecoder('utf-8', { fatal: true }).decode(buffer).replace(/^\uFEFF/, '');
        if (!content.trim()) throw new Error('这份笔记没有内容，请选择另一份文件。');
        const title = (content.match(/^#\s+(.+)$/m)?.[1] || file.name.replace(/\.md$/i, '')).trim().slice(0, 160);
        const data = { title, filename: file.name, content };
        const existing = docs.find(doc => doc.filename === file.name);
        importBusy = false;
        if (existing) {
          pendingReplace = { data, existing };
          documentMessage = '同名文档需要确认替换，请在文档页查看。';
          if (paneIsOpen('documents')) renderDocuments();
          else shell.notify(documentMessage);
        }
        else commitImport(data);
      } catch (error) {
        importBusy = false;
        documentMessage = error instanceof TypeError ? '文件编码不是有效的 UTF-8，请转换后重新导入。' : error.message;
        if (paneIsOpen('documents')) renderDocuments();
        else shell.notify(documentMessage);
      }
    }
    function deleteDocument() {
      const removed = docById(pendingDelete);
      if (!removed) return;
      if (liveMode) {
        mutateLiveDocuments(() => backend.deleteDocument(removed.id, removed.revisionId), `已删除《${removed.title}》。`);
        return;
      }
      docs = docs.filter(doc => doc.id !== removed.id);
      reviews = reviews.filter(review => review.docId !== removed.id);
      delete progress[removed.id];
      for (const chat of history.conversations) {
        if (chat.quizDocId === removed.id) {
          chat.quizDocId = null;
          chat.quizResult = null;
        }
      }
      if (sourceDocId === removed.id) sourceDocId = null;
      pendingDelete = null;
      documentMessage = `已删除《${removed.title}》。`;
      activity(documentMessage);
      persist();
      shell.documentsChanged(docs);
      renderDocuments();
    }
    async function readDocument(id) {
      captureLearning();
      const doc = docById(id);
      if (!doc) { shell.notify('这份文档已经移除。'); return; }
      shell.selectDocument(id);
      const request = ++readerRequest;
      if (liveMode) {
        if (!doc.snapshot) { shell.notify(doc.parseError || '请先建立索引，再打开这份文档的原文。'); return; }
        renderDocumentReader(doc, notice('正在读取原文…'));
        try {
          const source = await backend.readSource(doc.id, doc.snapshot);
          if (request !== readerRequest || !paneIsOpen('reader')) return;
          renderDocumentReader({ ...doc, content: source.content });
        } catch (error) {
          if (request === readerRequest && paneIsOpen('reader')) renderDocumentReader(doc, notice(error.message));
        }
        return;
      }
      renderDocumentReader(doc);
    }
    function renderDocumentReader(doc, status = '') {
      const id = doc.id;
      show('reader', `<div class="reader-grid"><article class="reader-copy reveal"><p class="kicker">SOURCE / 引用原文</p><h1 class="pane-heading">${escape(doc.title)}</h1><p class="doc-meta">${escape(doc.filename)} · ${doc.content.length.toLocaleString()} 字 · 约 ${doc.minutes} 分钟</p><div class="reader-content">${status || markdown(doc.content.replace(/^#\s+[^\n]+\n*/, ''))}</div><div class="row reader-actions">${button('learn-doc', '问答与学习', 'primary', `data-id="${escape(doc.id)}"`)}${button('source-back', '关闭')}</div></article><div class="poster-wrap">${shell.posterMarkup(doc.artId || 'noon')}</div></div>`, { title: '引用原文', layout: 'reader', docId: id });
    }
    async function refreshDueCount() {
      try {
        const result = await backend.reviews(true);
        liveDueCount = result.items.length;
        shell.refreshCounter();
      } catch { /* The review page reports request errors. */ }
    }
    async function refreshLiveReviews() {
      const dueOnly = !showAllReviews;
      try {
        const result = await backend.reviews(dueOnly);
        if (dueOnly !== !showAllReviews) return;
        liveReviews = result.items;
        liveDueCount = result.items.filter(item => item.is_due).length;
        shell.refreshCounter();
      } catch (error) { reviewMessage = error.message; }
      if (paneIsOpen('reviews')) renderReviews();
    }
    async function openLiveReview(id) {
      if (liveReviewRequest?.pending) return;
      const item = liveReviews.find(review => review.item_id === id);
      if (!item?.is_due) return;
      expandedReview = id;
      liveReviewRequest = null;
      liveReviewQuestion = null;
      reviewMessage = '正在读取复习题…';
      renderReviews();
      try {
        const quiz = await backend.reviewQuestion(id);
        if (expandedReview !== id) return;
        liveReviewQuestion = { quiz, item };
        reviewMessage = '';
      } catch (error) { if (expandedReview === id) reviewMessage = error.message; }
      if (paneIsOpen('reviews')) renderReviews();
    }
    async function submitLiveReview() {
      if (!liveReviewQuestion || liveReviewRequest?.pending) return;
      const selected = host.querySelector('input[name="live-review-answer"]:checked');
      if (!selected) { shell.notify('请先选择一个答案。'); return; }
      const { item } = liveReviewQuestion;
      const request = liveReviewRequest ??= { id: crypto.randomUUID(), answer: selected.value };
      request.pending = true;
      renderReviews();
      try {
        const result = await backend.answerReview(item.item_id, { request_id: request.id, answer: request.answer, expected_review_at: item.next_review_at });
        reviewMessage = `本次得分 ${result.result.score}/${result.result.max_score}。${result.result.explanation} 下次复习 ${date(result.schedule.next_review_at)}。`;
        expandedReview = null;
        liveReviewQuestion = null;
        liveReviewRequest = null;
        await refreshLiveReviews();
      } catch (error) {
        request.pending = false;
        reviewMessage = error.message;
        if (paneIsOpen('reviews')) renderReviews();
      }
    }
    function renderLiveReviews() {
      const busy = Boolean(liveReviewRequest?.pending);
      const rows = liveReviews.map(item => {
        const quiz = expandedReview === item.item_id ? liveReviewQuestion?.quiz : null;
        const choices = quiz ? Object.entries(quiz.options).map(([key, label]) => `<label class="quiz-option"><input type="radio" name="live-review-answer" value="${escape(key)}" ${liveReviewRequest?.answer === key ? 'checked' : ''} ${liveReviewRequest ? 'disabled' : ''}><span>${escape(label)}</span></label>`).join('') : '';
        const form = quiz ? `<div class="quiz-panel"><h3>${escape(quiz.question)}</h3><div class="quiz-options">${choices}</div><div class="row">${button('submit-review', busy ? '正在提交…' : '提交复习答案', 'primary', busy ? 'disabled' : '')}${button('close-review', '收起复习', '', busy ? 'disabled' : '')}</div></div>` : '';
        return `<article class="review-item"><div class="panel-head"><div><h2>${escape(item.title)}</h2><p class="doc-meta">${date(item.next_review_at)} · ${item.is_due ? '已到期' : '尚未到期'}</p></div>${button('open-review', '打开题目', 'small', `data-id="${escape(item.item_id)}" ${item.is_due && !busy ? '' : 'disabled'}`)}</div>${form}</article>`;
      }).join('');
      show('reviews', `${heading('REVIEW / 到期复习', '到期复习', button('refresh-reviews', '刷新', '', busy ? 'disabled' : ''))}<div class="panel reveal"><label class="check-row"><input id="all-reviews" type="checkbox" ${showAllReviews ? 'checked' : ''}> 显示全部计划</label>${notice(reviewMessage)}${rows || '<p class="form-hint">当前没有可显示的复习计划。</p>'}</div>`, { title: '到期复习' });
    }
    function renderReviews() {
      if (liveMode) { renderLiveReviews(); return; }
      const now = Date.now();
      const valid = reviews.filter(review => docById(review.docId));
      const visible = valid.filter(review => showAllReviews || review.dueAt <= now || review.id === expandedReview);
      show('reviews', `${heading('RECALL / 到期复习', '到期复习', button('refresh-reviews', '刷新复习'))}<div class="review-toolbar reveal"><label class="check-label"><input id="all-reviews" type="checkbox" ${showAllReviews ? 'checked' : ''}> 显示全部计划</label><p class="form-hint">${date(now)} · ${valid.filter(review => review.dueAt <= now).length} 项到期 · 演示学习计划</p></div>${notice(reviewMessage)}<div class="review-list reveal">${visible.length ? visible.map(review => {
        const doc = docById(review.docId);
        const due = review.dueAt <= now;
        const quiz = quizzes[doc.id] || defaultQuiz(doc);
        const expanded = expandedReview === review.id;
        return `<article class="review-item"><div class="panel-head"><div><p class="kicker">${due ? 'DUE / 待复习' : 'SCHEDULED / 已计划'}</p><h2>${escape(doc.title)}</h2><p class="doc-meta">${date(review.dueAt)} · 已练习 ${review.attempts} 次 · 上次 ${review.score} 分</p></div>${button('open-review', due ? '复习这道题' : '尚未到期', 'small', `data-id="${escape(review.id)}" ${due ? '' : 'disabled'}`)}</div>${expanded ? `<div class="quiz-panel"><h3>${escape(quiz.question)}</h3><div class="quiz-options">${quiz.options.map((label, i) => `<label class="quiz-option"><input type="radio" name="review-answer" value="${i}" ${reviewSelection === i ? 'checked' : ''} ${reviewResult !== null ? 'disabled' : ''}><span>${escape(label)}</span></label>`).join('')}</div>${reviewResult !== null ? `<p class="notice">${reviewResult === 100 ? '回答正确 · 100 分' : '本次得分 · 0 分'}。${escape(quiz.explanation)} 下次复习 ${date(review.dueAt)}。</p>` : ''}<div class="row">${reviewResult === null ? button('submit-review', '提交复习答案', 'primary') : ''}${button('close-review', '收起复习')}</div></div>` : ''}</article>`;
      }).join('') : '<div class="empty-state"><span class="empty-symbol">✓</span><h3>今天的复习已完成。</h3><p>勾选“显示全部计划”查看接下来的安排。</p></div>'}</div>`, { title: '到期复习' });
    }
    function submitReview() {
      const selected = host.querySelector('input[name="review-answer"]:checked');
      if (!selected) { shell.notify('请先选择一个答案。'); return; }
      const review = reviews.find(item => item.id === expandedReview);
      const doc = review && docById(review.docId);
      if (!doc || reviewResult !== null) return;
      reviewSelection = Number(selected.value);
      const quiz = quizzes[doc.id] || defaultQuiz(doc);
      reviewResult = reviewSelection === quiz.correct ? 100 : 0;
      recordAnswer(doc, reviewResult);
      // Keep the answered question visible until the learner collapses it.
      renderReviews();
    }
    async function refreshLiveProgress() {
      try { liveProgress = await backend.progress(); progressMessage = ''; }
      catch (error) { progressMessage = error.message; }
      if (paneIsOpen('progress')) renderProgress();
    }
    function renderLiveProgress() {
      const summary = liveProgress?.summary;
      const statuses = { unassessed: '尚无答题证据', needs_review: '需要复习', practicing: '正在巩固', stable: '表现稳定' };
      const rows = liveProgress?.concepts.map(concept => `<details class="progress-row"><summary><span><strong>${escape(concept.label)}</strong><span class="doc-meta">${concept.total_attempts} 次答题</span></span><span class="progress-state">${escape(statuses[concept.mastery.status])}</span><span class="meter" aria-hidden="true"><span style="width:${Number(concept.average_score) || 0}%"></span></span><span aria-hidden="true">＋</span></summary><div class="progress-detail"><p>讲解 ${concept.taught_count} 次 · 平均 ${concept.average_score} 分 · 满分率 ${concept.full_score_rate}%</p><p class="form-hint">${escape(concept.mastery.reason)}</p></div></details>`).join('');
      show('progress', `${heading('PROGRESS / 学习进度', '学习进度', button('refresh-progress', '刷新'))}<div class="panel reveal">${notice(progressMessage)}${summary ? `<p>${summary.total_attempts} 次答题 · 平均 ${summary.average_score} 分 · 满分率 ${summary.full_score_rate}%</p>` : ''}${rows || '<p class="form-hint">讲解和练习完成后，这里会显示知识点进度。</p>'}</div>`, { title: '学习进度' });
    }
    function renderProgress() {
      if (liveMode) { renderLiveProgress(); return; }
      const scores = docs.flatMap(doc => Array.isArray(progress[doc.id]) ? progress[doc.id] : []);
      const average = scores.length ? Math.round(scores.reduce((sum, score) => sum + score, 0) / scores.length) : 0;
      const full = scores.length ? Math.round(scores.filter(score => score === 100).length / scores.length * 100) : 0;
      show('progress', `${heading('GROWTH / 学习进度', '学习进度', button('refresh-progress', '刷新进度'))}<div class="progress-summary reveal"><div><strong>${scores.length}</strong><span>次答题</span></div><div><strong>${average}<small>分</small></strong><span>平均分</span></div><div><strong>${full}<small>%</small></strong><span>满分率</span></div></div><p class="form-hint reveal">演示学习记录 · 完成练习后更新</p><div class="progress-list reveal">${docs.map(doc => {
        const attempts = Array.isArray(progress[doc.id]) ? progress[doc.id] : [];
        const mean = attempts.length ? Math.round(attempts.reduce((sum, value) => sum + value, 0) / attempts.length) : 0;
        const rate = attempts.length ? Math.round(attempts.filter(value => value === 100).length / attempts.length * 100) : 0;
        const status = !attempts.length ? '尚无答题证据' : mean < 60 ? '需要复习' : mean >= 80 && attempts.length >= 3 ? '表现稳定' : '正在巩固';
        return `<details class="progress-row"><summary><span><strong>${escape(doc.title)}</strong><span class="doc-meta">${attempts.length} 次答题</span></span><span class="progress-state">${status}</span><span class="meter" aria-hidden="true"><span style="width:${mean}%"></span></span><span aria-hidden="true">＋</span></summary><div class="progress-detail"><p>答题 ${attempts.length} 次 · 平均 ${mean} 分 · 满分率 ${rate}%</p><p class="form-hint">${attempts.length ? '本页数据来自演示记录与当前浏览器内完成的练习。' : '完成一次独立练习后，这里会出现答题记录。'}</p>${button('learn-doc', '问答与学习', 'small', `data-id="${escape(doc.id)}"`)}</div></details>`;
      }).join('')}</div>`, { title: '学习进度' });
    }
    async function connectBackend(accessToken = '') {
      if (connectionPending) return;
      connectionPending = true;
      connectionMessage = '正在连接本机后端…';
      if (paneIsOpen('settings')) renderSettings();
      try {
        backend.setToken(accessToken);
        const health = await backend.health();
        if (health.service !== 'personal-knowledge-base') throw new Error('该地址没有运行个人知识库后端。');
        connected = true;
        await refreshLiveDocuments();
        connectionMessage = `已连接本机后端 · ${docs.length} 份文档。`;
        activity(connectionMessage);
        if (paneIsOpen('reviews')) refreshLiveReviews();
        else refreshDueCount();
        if (paneIsOpen('progress')) refreshLiveProgress();
      } catch (error) {
        connected = false;
        connectionMessage = '连接失败 · ' + error.message;
        activity(connectionMessage);
      } finally {
        connectionPending = false;
        if (paneIsOpen('settings')) renderSettings();
        showDocumentStatus();
      }
    }
    function renderSettings() {
      show('settings', `${heading('CONNECTION / 连接设置', '连接设置')}<div class="pane-grid settings-grid"><section class="panel reveal"><form id="settings-form"><label class="field" for="api">后端地址<input id="api" type="url" value="${escape(settings.api)}" placeholder="http://127.0.0.1:8000" spellcheck="false" required ${liveMode ? 'readonly' : ''}></label><label class="field" for="access-token">访问密钥</label><div class="token-field"><input id="access-token" type="${tokenVisible ? 'text' : 'password'}" value="${escape(token)}" autocomplete="off" spellcheck="false" placeholder="${liveMode ? '本机可自动获取' : '输入演示密钥'}"><button class="btn small" data-action="toggle-token" type="button">${tokenVisible ? '隐藏密钥' : '显示密钥'}</button></div><button class="btn primary" type="submit" ${connectionPending ? 'disabled' : ''}>${connectionPending ? '正在连接…' : '保存并连接'}</button></form>${notice(connectionMessage)}<p class="form-hint">${liveMode ? '页面和 API 由同一个本机服务提供。手动输入的访问密钥仅保留在当前页面。' : '本地 Demo 使用示例数据，访问密钥仅保留在当前页面。'}</p><a class="source-link" href="${liveMode ? '/docs' : 'http://127.0.0.1:8000/'}" target="_blank" rel="noopener noreferrer"><span>${liveMode ? '查看后端接口' : '打开本机知识库'}</span><span>↗</span></a></section><section class="panel reveal"><p class="kicker">ACTIVITY / 运行记录</p><details class="activity-details" open><summary>运行记录</summary><pre class="activity-log">${escape(log.join('\n'))}</pre></details></section></div>`, { title: '连接设置' });
    }
    function saveSettings() {
      const value = document.getElementById('api').value.trim();
      const accessToken = document.getElementById('access-token').value;
      try {
        const url = new URL(value);
        if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash || /[\u0000-\u0020\u007f\\]/.test(value)) throw new Error();
        settings.api = url.origin + url.pathname.replace(/\/+$/, '');
      } catch { connectionMessage = '请输入有效的 http 或 https 后端地址。'; renderSettings(); return; }
      token = accessToken;
      tokenVisible = false;
      if (liveMode) { connectBackend(token); return; }
      connectionMessage = '演示配置已保存。当前页面使用本地示例数据。';
      activity('已保存演示后端地址。');
      persist();
      renderSettings();
      shell.notify('演示配置已保存。');
    }
    function openPane(name) {
      captureLearning();
      readerRequest++;
      if (pane !== name) learningOverlay = '';
      if (name === 'learning') renderLearning();
      else if (name === 'documents') renderDocuments();
      else if (name === 'reviews') { renderReviews(); if (liveMode) refreshLiveReviews(); }
      else if (name === 'progress') { renderProgress(); if (liveMode) refreshLiveProgress(); }
      else if (name === 'settings') renderSettings();
    }
    function openImport() {
      openPane('documents');
      document.getElementById('file')?.focus({ preventScroll: true });
    }
    const examples = {
      'example-hybrid': '混合检索里，BM25 和向量检索各有什么作用？',
      'example-index': '修改笔记以后，怎样更新知识库索引？',
      'example-memory': '个人知识库的长期记忆准备如何接入？'
    };
    host.addEventListener('submit', event => {
      if (event.target.id === 'learning-form') { event.preventDefault(); ask(); }
      else if (event.target.id === 'settings-form') { event.preventDefault(); saveSettings(); }
    });
    host.addEventListener('input', event => {
      if (event.target.id === 'question') session.question = event.target.value;
      if (event.target.id === 'access-token') token = event.target.value;
      if (event.target.id === 'api') settings.api = event.target.value;
    });
    host.addEventListener('change', event => {
      if (event.target.id === 'file') {
        selectedFile = event.target.files[0] || null;
        const label = host.querySelector('.file-drop strong');
        if (label) label.textContent = selectedFile?.name || '选择一份 Markdown 笔记';
      } else if (event.target.id === 'all-reviews') { showAllReviews = event.target.checked; renderReviews(); if (liveMode) refreshLiveReviews(); }
      else if (event.target.name === 'quiz-answer') session.selection = Number(event.target.value);
      else if (event.target.name === 'live-quiz-answer') liveSelection = event.target.value;
      else if (event.target.name === 'review-answer') reviewSelection = Number(event.target.value);
      else if (['question', 'learning-mode', 'teaching-mode'].includes(event.target.id)) { captureLearning(); persist(); }
    });
    host.addEventListener('pointerdown', event => {
      const handle = event.target.closest('.source-resizer');
      if (!handle || compactLearning.matches || event.button !== 0 || sourceDrag) return;
      const workspace = handle.closest('.learning-workspace');
      sourceDrag = {
        pointerId: event.pointerId, startX: event.clientX, workspace, handle,
        initialWidth: workspace.querySelector('.chat-source').offsetWidth,
        maximum: sourceWidthLimit(workspace)
      };
      handle.focus({ preventScroll: true });
      handle.setPointerCapture(event.pointerId);
      workspace.dataset.resizing = '';
      event.preventDefault();
    });
    host.addEventListener('pointermove', event => {
      if (!sourceDrag || sourceDrag.pointerId !== event.pointerId) return;
      const { workspace, initialWidth, startX, maximum } = sourceDrag;
      sourceWidth = applySourceWidth(workspace, initialWidth + startX - event.clientX, maximum);
    });
    function cancelSourceDrag() {
      if (!sourceDrag) return;
      const { workspace, handle, pointerId } = sourceDrag;
      sourceDrag = null;
      delete workspace.dataset.resizing;
      if (handle.hasPointerCapture(pointerId)) handle.releasePointerCapture(pointerId);
    }
    function stopSourceDrag(event) {
      if (sourceDrag?.pointerId === event.pointerId) cancelSourceDrag();
    }
    host.addEventListener('pointerup', stopSourceDrag);
    host.addEventListener('pointercancel', stopSourceDrag);
    host.addEventListener('lostpointercapture', stopSourceDrag);
    window.addEventListener('resize', () => {
      const workspace = host.querySelector('.learning-workspace');
      if (workspace) applySourceWidth(workspace);
    });
    compactLearning.addEventListener('change', event => {
      if (!event.matches) learningOverlay = '';
      else cancelSourceDrag();
      const workspace = host.querySelector('.learning-workspace');
      if (workspace) updateLearningPanels(workspace);
    });
    host.addEventListener('keydown', event => {
      if (pane !== 'learning') return;
      if (event.key === 'Escape' && compactLearning.matches && learningOverlay) {
        event.preventDefault();
        event.stopPropagation();
        toggleLearningOverlay('');
      } else if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && event.target.closest('#learning-form')) {
        event.preventDefault();
        ask();
      } else if (event.target.matches('.source-resizer')) {
        const workspace = event.target.closest('.learning-workspace');
        const width = Number(event.target.getAttribute('aria-valuenow'));
        const maximum = sourceWidthLimit(workspace);
        const sizes = { ArrowLeft: width + 24, ArrowRight: width - 24, Home: 270, End: maximum };
        if (!Object.hasOwn(sizes, event.key)) return;
        event.preventDefault();
        sourceWidth = applySourceWidth(workspace, sizes[event.key], maximum);
      }
    });
    host.addEventListener('click', event => {
      const control = event.target.closest('[data-action]');
      if (!control || control.disabled) return;
      const action = control.dataset.action;
      const id = control.dataset.id;
      if (examples[action]) { session.question = examples[action]; const input = document.getElementById('question'); input.value = session.question; input.focus(); return; }
      if (action === 'new-session') newSession();
      else if (action === 'select-chat') selectChat(id);
      else if (action === 'open-history') toggleLearningOverlay('history');
      else if (action === 'open-source') toggleLearningOverlay('source');
      else if (action === 'close-overlay') toggleLearningOverlay('');
      else if (action === 'close-source') closeLearningSource();
      else if (action === 'live-citation') openLiveCitation(Number(control.dataset.turn), Number(control.dataset.citation));
      else if (action === 'retry-learning') resumeLiveLearning('retry');
      else if (action.startsWith('live-learning-')) resumeLiveLearning(action.slice('live-learning-'.length));
      else if (action === 'exercise') exercise();
      else if (action === 'variant') exercise(true);
      else if (action === 'submit-quiz') submitQuiz();
      else if (action === 'remediate') { session.turns.at(-1).answer.html += `<p>回到原文再看一次。${escape(learningQuiz(docById(session.quizDocId)).explanation)}</p>`; session.quizDocId = null; session.quizResult = null; renderLearning({ scrollToEnd: true }); persist(); }
      else if (action === 'finish') { session.finished = true; session.quizDocId = null; persist(); renderLearning(); shell.notify('本轮学习已结束，演示记录已保存。'); }
      else if (action === 'read') readDocument(id);
      else if (action === 'citation') openLearningSource(id);
      else if (action === 'source-back') openPane('documents');
      else if (action === 'learn-doc') { const doc = docById(id); if (doc) newSession(`请根据笔记讲解${doc.title}。`); }
      else if (action === 'upload') importDocument();
      else if (action === 'delete') { pendingDelete = id; pendingReplace = null; renderDocuments(); }
      else if (action === 'cancel-delete') { pendingDelete = null; renderDocuments(); }
      else if (action === 'confirm-delete') deleteDocument();
      else if (action === 'confirm-replace' && pendingReplace) { if (liveMode) uploadLiveDocument(pendingReplace.file, true); else commitImport(pendingReplace.data, pendingReplace.existing); }
      else if (action === 'cancel-replace') { pendingReplace = null; documentMessage = '已取消替换。'; renderDocuments(); }
      else if (action === 'refresh-documents' && liveMode) refreshDocumentsFromServer();
      else if (action === 'refresh-documents') { documentMessage = `已刷新当前浏览器中的 ${docs.length} 份演示文档。`; renderDocuments(); shell.notify('文档目录已刷新。'); }
      else if (action === 'reindex' && liveMode) mutateLiveDocuments(() => backend.reindex(), '索引已重建。');
      else if (action === 'reindex') { docs = docs.map(doc => ({ ...doc, indexed: true })); documentMessage = `已完成 ${docs.length} 份文档的演示索引重建。`; persist(); activity(documentMessage); shell.documentsChanged(docs); renderDocuments(); }
      else if (action === 'refresh-reviews' && liveMode) refreshLiveReviews();
      else if (action === 'refresh-reviews') { reviewMessage = '复习计划已刷新。'; renderReviews(); shell.notify('复习计划已刷新。'); }
      else if (action === 'open-review' && liveMode) openLiveReview(id);
      else if (action === 'open-review') { expandedReview = id; reviewSelection = null; reviewResult = null; renderReviews(); }
      else if (action === 'submit-review') { if (liveMode) submitLiveReview(); else submitReview(); }
      else if (action === 'close-review') { expandedReview = null; liveReviewQuestion = null; liveReviewRequest = null; reviewResult = null; renderReviews(); }
      else if (action === 'refresh-progress' && liveMode) refreshLiveProgress();
      else if (action === 'refresh-progress') { renderProgress(); shell.notify('学习进度已刷新。'); }
      else if (action === 'toggle-token') { token = document.getElementById('access-token').value; tokenVisible = !tokenVisible; const input = document.getElementById('access-token'); input.type = tokenVisible ? 'text' : 'password'; control.textContent = tokenVisible ? '隐藏密钥' : '显示密钥'; }
    });

    if (liveMode) {
      backend = window.createKnowledgeApi({ baseUrl: location.origin });
      queueMicrotask(() => connectBackend());
    }
    return { getDocuments: () => docs, openPane, readDocument, newSession, openImport, getDueCount: () => liveMode ? liveDueCount : reviews.filter(item => docById(item.docId) && item.dueAt <= Date.now()).length };
  };
}());
