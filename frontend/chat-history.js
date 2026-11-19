(function () {
  'use strict';

  const isRecord = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const questionText = value => typeof value === 'string' ? value.slice(0, 4000) : '';

  window.createLearningHistory = function createLearningHistory(stored, docs) {
    const saved = stored?.version === 1 ? stored : {};
    const docIds = new Set(docs.map(doc => doc.id));
    const conversations = [];
    const usedIds = new Set();
    let idCounter = 0;

    function nextId() {
      const prefix = `chat-${Date.now().toString(36)}-`;
      let id;
      do { id = prefix + idCounter++; } while (usedIds.has(id));
      return id;
    }

    function createConversation(id, values = {}) {
      const quizDocId = docIds.has(values.quizDocId) ? values.quizDocId : null;
      return {
        id,
        updatedAt: Number.isFinite(values.updatedAt) && Math.abs(values.updatedAt) <= 8.64e15 ? values.updatedAt : Date.now(),
        mode: ['', 'teach', 'assess', 'direct'].includes(values.mode) ? values.mode : '',
        teaching: values.teaching === 'solve_step' ? 'solve_step' : 'explain',
        question: questionText(values.question),
        turns: [],
        backendId: typeof values.backendId === 'string' && /^[A-Za-z0-9_-]+$/.test(values.backendId) ? values.backendId : null,
        quizDocId,
        quizVersion: quizDocId && Number.isInteger(values.quizVersion) && values.quizVersion >= 0 ? values.quizVersion : 0,
        selection: quizDocId && Number.isInteger(values.selection) && values.selection >= 0 && values.selection < 3 ? values.selection : null,
        quizResult: quizDocId && [0, 100].includes(values.quizResult) ? values.quizResult : null,
        finished: values.finished === true
      };
    }

    function restoreAnswer(answer) {
      if (isRecord(answer) && Array.isArray(answer.parts) && answer.parts.every(part => typeof part === 'string')) {
        return {
          parts: answer.parts,
          basis: typeof answer.basis === 'string' ? answer.basis : '',
          citations: Array.isArray(answer.citations) ? answer.citations.filter(isRecord) : [],
          quiz: isRecord(answer.quiz) && isRecord(answer.quiz.options) ? answer.quiz : null,
          grading: isRecord(answer.grading) ? answer.grading : null,
          pending: isRecord(answer.pending) && Array.isArray(answer.pending.actions) ? answer.pending : null
        };
      }
      if (!isRecord(answer) || typeof answer.html !== 'string'
        || !Array.isArray(answer.docIds) || !answer.docIds.every(id => typeof id === 'string')
        || /<(?!\/?(?:p|ol|li|code)\s*>)/i.test(answer.html)) return null;
      return { html: answer.html, docIds: answer.docIds.filter(id => docIds.has(id)) };
    }

    function restoreTurns(chat, storedTurns) {
      if (!Array.isArray(storedTurns)) return;
      let interruptedQuestion = '';
      for (const turn of storedTurns) {
        if (!isRecord(turn)) continue;
        const question = questionText(turn.question).trim();
        if (!question) continue;
        const answer = restoreAnswer(turn.answer);
        const savedRequest = turn.request;
        const request = isRecord(savedRequest) && typeof savedRequest.request_id === 'string'
          && ['start', 'answer', 'exercise', 'remediate', 'variant', 'finish'].includes(savedRequest.action)
          ? savedRequest : null;
        if (request) chat.turns.push({ question, answer, request, error: '上次请求尚未确认，请重试。' });
        else if (answer) chat.turns.push({ question, answer, error: typeof turn.error === 'string' ? turn.error : '' });
        else if (turn.answer === null) interruptedQuestion = question;
      }
      if (!chat.question.trim()) chat.question = interruptedQuestion;
    }

    function addConversation(chat) {
      usedIds.add(chat.id);
      conversations.push(chat);
      return chat;
    }

    if (Array.isArray(saved.conversations)) {
      for (const item of saved.conversations) {
        if (!isRecord(item) || typeof item.id !== 'string' || !item.id.trim() || usedIds.has(item.id)) continue;
        const chat = createConversation(item.id, item);
        restoreTurns(chat, item.turns);
        if (chat.turns.length || chat.question.trim() || item.id === saved.activeId) addConversation(chat);
      }
    }

    if (!conversations.length && isRecord(saved.session)) {
      const chat = createConversation(nextId(), saved.session);
      const answer = restoreAnswer(saved.session.answer);
      if (chat.question.trim() && answer) {
        chat.turns.push({ question: chat.question.trim(), answer });
        chat.question = '';
      }
      addConversation(chat);
    }

    let active = conversations.find(chat => chat.id === saved.activeId)
      || conversations.reduce((latest, chat) => !latest || chat.updatedAt > latest.updatedAt ? chat : latest, null)
      || addConversation(createConversation(nextId()));

    return {
      conversations,
      get active() { return active; },
      get activeId() { return active.id; },
      select(id) {
        const selected = conversations.find(chat => chat.id === id);
        if (!selected) return false;
        // Blank workspaces need not be kept after switching to an existing chat.
        if (active !== selected && !active.turns.length && !active.question.trim()) {
          conversations.splice(conversations.indexOf(active), 1);
          usedIds.delete(active.id);
        }
        active = selected;
        return true;
      },
      newChat() {
        if (active.turns.length || active.question.trim()) active = addConversation(createConversation(nextId()));
        return active;
      }
    };
  };
}());
