(function () {
  'use strict';
  const { SHEET, CROP } = window.DEMO_ART;
  const $ = selector => document.querySelector(selector);
  const shelf = $('.shelf');
  const stage = $('.stage');
  const sheet = $('.sheet');
  const experience = $('#experience');
  const host = $('#sheet-content');
  const cursor = $('.cursor');
  const play = $('.play');
  const tabs = [...document.querySelectorAll('.decade')];
  const captionMasks = [...document.querySelectorAll('.caption-mask')];
  const motionQuery = matchMedia('(prefers-reduced-motion:reduce)');
  const pointerQuery = matchMedia('(pointer:fine)');
  const EASE = 'cubic-bezier(.2,.8,.2,1)';
  const PAPER_EASE = 'cubic-bezier(.4,0,.2,1)';
  const mod = (value, length) => ((value % length) + length) % length;
  const smoothstep = value => value * value * (3 - 2 * value);
  let reduced = motionQuery.matches;
  let docs = [], tapes = [], count = 0, loop = 0;
  let W = innerWidth, V = innerHeight, H = 0, ws = 0, d = 0, gap = 0;
  let s = 17, T = 17, active = 5, rounded = 17, hover = -1;
  let mx = W / 2, my = V / 2, cx = mx, cy = my;
  let tiltX = 0, tiltY = 0, onCover = false, overButton = false;
  let isOpen = false, phase = 'closed', pane = 'documents';
  let drag = null, suppressClick = false, wheelTimer = 0, previousTime = 0;
  let sheetAnimation = null, focusBeforeOpen = null, toastAnimation = null;
  let inkEntries = [], posterSequence = 0, app = null;

  Object.entries(SHEET).forEach(([id, sheet]) => {
    // Short image URLs keep multi-megabyte data strings out of style recalculation.
    const binary = atob(sheet[0].slice(sheet[0].indexOf(',') + 1));
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    const url = URL.createObjectURL(new Blob([bytes], { type: 'image/webp' }));
    sheet[0] = url;
    stage.style.setProperty(`--art-${id}`, `url("${url}")`);
    const image = new Image();
    image.src = url;
  });
  const relative = v => loop ? mod(v - s + loop / 2, loop) - loop / 2 : 0;
  const currentDoc = () => docs[active] || docs[0];

  function applyCrop(element, key) {
    const [sheetId, x, y, w, h] = CROP[key];
    const [, width, height] = SHEET[sheetId];
    element.style.backgroundImage = `var(--art-${sheetId})`;
    element.style.backgroundSize = `${width / w * 100}% ${height / h * 100}%`;
    element.style.backgroundPosition = `${x / (width - w) * 100}% ${y / (height - h) * 100}%`;
  }

  function updateIndicator(name = pane) {
    const targetPane = name === 'reader' ? 'documents' : name;
    const selected = tabs.find(tab => tab.dataset.pane === targetPane) || tabs[1];
    tabs.forEach(tab => {
      if (tab === selected) tab.setAttribute('aria-current', 'page');
      else tab.removeAttribute('aria-current');
    });
    $('.decade-indicator').style.width = `${selected.offsetWidth}px`;
    $('.decade-indicator').style.transform = `translateX(${selected.offsetLeft}px)`;
    document.querySelectorAll('.sheet-nav [data-pane]').forEach(button => {
      if (button.dataset.pane === targetPane) button.setAttribute('aria-current', 'page');
      else button.removeAttribute('aria-current');
    });
  }

  function measure() {
    W = innerWidth;
    V = innerHeight;
    const probe = document.createElement('div');
    probe.style.cssText = 'position:fixed;left:-10000px;height:var(--H);pointer-events:none';
    document.body.append(probe);
    H = probe.getBoundingClientRect().height;
    probe.remove();
    ws = H * 142 / 840;
    d = H * 474 / 900;
    gap = H * .03;
    stage.style.setProperty('--ws', `${ws}px`);
    stage.style.setProperty('--d', `${d}px`);
    play.style.left = `${W / 2 - d * .5 - 6}px`;
    play.style.top = `${V / 2 + H * .36}px`;
    updateIndicator();
  }

  function swapLine(mask, text, direction, instant = false) {
    if (reduced || instant) {
      mask.getAnimations({ subtree: true }).forEach(animation => animation.cancel());
      const span = document.createElement('span');
      span.textContent = text;
      mask.replaceChildren(span);
      return;
    }
    [...mask.children].forEach(old => {
      if (old.dataset.leaving) return;
      old.dataset.leaving = 'true';
      old.setAttribute('aria-hidden', 'true');
      const from = getComputedStyle(old).transform;
      old.getAnimations().forEach(animation => animation.cancel());
      old.animate([{ transform: from }, { transform: `translateY(${-direction * 105}%)` }], { duration: 650, easing: EASE, fill: 'both' }).onfinish = () => old.remove();
    });
    const span = document.createElement('span');
    span.textContent = text;
    mask.append(span);
    span.animate([{ transform: `translateY(${direction * 105}%)` }, { transform: 'translateY(0)' }], { duration: 650, easing: EASE, fill: 'both' });
  }

  function setActive(index, direction = 1, instant = false) {
    active = index;
    const doc = currentDoc();
    if (!doc) return;
    const values = [doc.title, doc.topic || 'MARKDOWN / 我的笔记', `MARKDOWN / ${doc.minutes || 1} 分钟`];
    values.forEach((text, i) => swapLine(captionMasks[i], text, direction, instant));
    play.setAttribute('aria-label', `阅读文档，${doc.title}`);
  }

  function refreshCounter() {
    const due = app?.getDueCount?.() || 0;
    $('#library-status').textContent = `${docs.length} 份文档 / ${due} 项待复习`;
  }

  function buildShelf(newDocs) {
    const selectedId = currentDoc()?.id;
    const previousIndex = active;
    docs = newDocs;
    count = docs.length;
    loop = count * 3;
    hover = -1;
    stage.replaceChildren();
    $('.shelf-empty').hidden = count > 0;
    $('.caption').hidden = !count;
    play.hidden = !count;
    $('.arrow-prev').hidden = !count;
    $('.arrow-next').hidden = !count;
    if (!count) { tapes = []; refreshCounter(); return; }
    const retained = docs.findIndex(doc => doc.id === selectedId);
    active = retained >= 0 ? retained : Math.min(previousIndex, count - 1);
    s = T = rounded = count + active;
    tapes = Array.from({ length: loop }, (_, v) => {
      const doc = docs[v % count];
      const artId = CROP[`cover-${doc.artId}`] ? doc.artId : 'noon';
      const element = document.createElement('button');
      element.type = 'button';
      element.className = 'tape';
      element.dataset.v = v;
      element.setAttribute('aria-label', doc.title);
      element.tabIndex = -1;
      const spine = document.createElement('span');
      const cover = document.createElement('span');
      spine.className = 'face sp';
      cover.className = 'face cv';
      spine.setAttribute('aria-hidden', 'true');
      cover.setAttribute('aria-hidden', 'true');
      applyCrop(spine, `spine-${artId}`);
      applyCrop(cover, `cover-${artId}`);
      element.append(spine, cover);
      element.addEventListener('pointerenter', () => { hover = v; });
      element.addEventListener('pointerleave', () => { if (hover === v) hover = -1; });
      element.addEventListener('click', () => {
        if (isOpen || suppressClick) return;
        const r = relative(v);
        if (Math.abs(r) < .5) app.readDocument(doc.id);
        else T = Math.round(s + r);
      });
      stage.append(element);
      return { v, element, spine, cover, lift: 0 };
    });
    setActive(active, 1, true);
    refreshCounter();
    renderTapes(1);
  }

  function renderTapes(dt) {
    const liftEase = reduced ? 1 : 1 - Math.exp(-12 * dt);
    for (const tape of tapes) {
      const r = relative(tape.v);
      const b = Math.abs(r) < 1 ? (r + Math.sin(Math.PI * r) / Math.PI) / 2 : Math.sign(r) * .5;
      const x = r * (ws + gap) + (d - ws + 1.6 * gap) * b;
      const visible = Math.abs(x) <= W / 2 + 1.5 * d;
      if (tape.visible !== visible) {
        tape.visible = visible;
        tape.element.style.visibility = visible ? 'visible' : 'hidden';
        tape.element.setAttribute('aria-hidden', String(!visible));
      }
      const tabIndex = Math.abs(r) < .5 ? 0 : -1;
      if (tape.element.tabIndex !== tabIndex) tape.element.tabIndex = tabIndex;
      if (!visible) { tape.lift = 0; continue; }
      const o = smoothstep(Math.max(0, 1 - Math.abs(r)));
      const targetLift = !isOpen && hover === tape.v && Math.abs(r) > .5 ? 1 : 0;
      tape.lift += (targetLift - tape.lift) * liftEase;
      if (Math.abs(targetLift - tape.lift) < .001) tape.lift = targetLift;
      const y = -o * .025 * H - tape.lift * .045 * H;
      const transform = `translate3d(${x}px,${y}px,${o * .2 * H}px) rotateZ(${-4.5 * o}deg) rotateX(${tiltX * o}deg) rotateY(${-90 * o + tiltY * o}deg)`;
      if (tape.transform !== transform) {
        tape.transform = transform;
        tape.element.style.transform = transform;
      }
      const zIndex = Math.round(100 - Math.abs(r) * 4);
      if (tape.zIndex !== zIndex) {
        tape.zIndex = zIndex;
        tape.element.style.zIndex = zIndex;
      }
      if (tape.openness !== o) {
        tape.openness = o;
        tape.spine.style.setProperty('--shade', o * .55);
        tape.cover.style.setProperty('--shade', (1 - o) * .5);
      }
    }
  }

  function activeCoverRect() {
    if (!tapes.length) return { top: V / 2 - H / 2, left: W / 2 - d / 2, right: W / 2 + d / 2, bottom: V / 2 + H / 2, width: d, height: H };
    const tape = tapes.reduce((nearest, item) => Math.abs(relative(item.v)) < Math.abs(relative(nearest.v)) ? item : nearest);
    return tape.cover.getBoundingClientRect();
  }
  const coverClip = rect => `inset(${Math.max(0, rect.top)}px ${Math.max(0, W - rect.right)}px ${Math.max(0, V - rect.bottom)}px ${Math.max(0, rect.left)}px round 3px)`;

  function showPane(name, html, options = {}) {
    const changed = pane !== name;
    const focusedId = isOpen && !changed ? document.activeElement?.id : null;
    pane = name;
    updateIndicator();
    host.innerHTML = html;
    sheet.setAttribute('aria-label', options.title || '知识库工作台');
    sheet.dataset.layout = options.layout || 'workspace';
    if (changed || !isOpen) $('.sheet-scroll').scrollTop = 0;
    const firstOpen = !isOpen || phase === 'closing';
    if (firstOpen) {
      clearTimeout(wheelTimer);
      s = T = rounded = options.docId ? Math.round(T) : Math.round(s);
      if (count) setActive(mod(rounded, count), 1, true);
      renderTapes(1);
      const rect = activeCoverRect();
      focusBeforeOpen = document.activeElement;
      const from = phase === 'closing' ? { clipPath: getComputedStyle(sheet).clipPath, transform: getComputedStyle(sheet).transform } : { clipPath: coverClip(rect), transform: 'rotate(-4.5deg)' };
      sheetAnimation?.cancel();
      isOpen = true;
      phase = 'opening';
      experience.inert = true;
      sheet.style.visibility = 'visible';
      sheet.style.transformOrigin = `${rect.left + rect.width / 2}px ${rect.top + rect.height / 2}px`;
      sheet.setAttribute('aria-hidden', 'false');
      sheetAnimation = sheet.animate([from, { clipPath: 'inset(0px 0px 0px 0px round 0px)', transform: 'rotate(0deg)' }], { duration: reduced ? 1 : 600, easing: PAPER_EASE, fill: 'both' });
      sheetAnimation.onfinish = () => { if (phase === 'opening') phase = 'open'; };
      $('.close').focus({ preventScroll: true });
    }
    if (firstOpen || changed) {
      host.querySelectorAll('.reveal').forEach((element, index) => {
        element.animate([{ clipPath: 'inset(100% 0 0 0)', transform: 'translateY(30px)' }, { clipPath: 'inset(0% 0 0 0)', transform: 'translateY(0)' }], { duration: reduced ? 1 : firstOpen ? 340 : 260, delay: reduced ? 0 : firstOpen ? 160 + index * 40 : index * 35, easing: EASE, fill: 'both' });
      });
    }
    if (focusedId && !firstOpen) document.getElementById(focusedId)?.focus({ preventScroll: true });
    const inkStart = performance.now() + (firstOpen ? 240 : 80);
    inkEntries = [...host.querySelectorAll('svg[data-ink] mask circle')].map(circle => ({
      circle, start: inkStart + Number(circle.dataset.delay)
    }));
    if (reduced) {
      inkEntries.forEach(({ circle }) => circle.setAttribute('r', 720));
      inkEntries = [];
    }
  }

  function closePane() {
    if (phase === 'closed' || phase === 'closing') return;
    phase = 'closing';
    const rect = activeCoverRect();
    const from = { clipPath: getComputedStyle(sheet).clipPath, transform: getComputedStyle(sheet).transform };
    sheetAnimation?.cancel();
    sheetAnimation = sheet.animate([from, { clipPath: coverClip(rect), transform: 'rotate(-4.5deg)' }], { duration: reduced ? 1 : 380, easing: PAPER_EASE, fill: 'both' });
    sheetAnimation.onfinish = () => {
      sheet.style.visibility = 'hidden';
      sheet.setAttribute('aria-hidden', 'true');
      sheet.getAnimations({ subtree: true }).forEach(animation => animation.cancel());
      isOpen = false;
      phase = 'closed';
      experience.inert = false;
      inkEntries = [];
      const restore = focusBeforeOpen?.isConnected ? focusBeforeOpen : document.body;
      restore.focus({ preventScroll: true });
      pane = 'documents';
      updateIndicator();
      refreshCounter();
    };
  }

  function posterMarkup(artId) {
    const key = CROP[`cover-${artId}`] ? `cover-${artId}` : 'cover-noon';
    const [sheetId, x, y, w, h] = CROP[key];
    const [url, width, height] = SHEET[sheetId];
    const id = `ink-${++posterSequence}`;
    return `<svg class="poster" data-ink viewBox="0 0 474 900" aria-hidden="true" xmlns="http://www.w3.org/2000/svg"><defs><clipPath id="${id}-crop"><rect width="474" height="900"/></clipPath><filter id="${id}-filter" x="-50%" y="-50%" width="200%" height="200%" color-interpolation-filters="sRGB"><feTurbulence type="fractalNoise" baseFrequency=".011" numOctaves="3" seed="7" result="noise"/><feDisplacementMap in="SourceGraphic" in2="noise" scale="120" xChannelSelector="R" yChannelSelector="G"/></filter><mask id="${id}-mask" maskUnits="userSpaceOnUse" x="-200" y="-200" width="874" height="1300" style="mask-type:luminance"><g filter="url(#${id}-filter)" fill="white">${[[150,330,0],[340,600,120],[250,120,220],[90,760,300],[400,250,360],[260,860,420],[230,470,180]].map(([a,b,delay]) => `<circle cx="${a}" cy="${b}" r="0" data-delay="${delay}"/>`).join('')}</g></mask></defs><g clip-path="url(#${id}-crop)"><g mask="url(#${id}-mask)"><image href="${url}" x="${-x * 474 / w}" y="${-y * 900 / h}" width="${width * 474 / w}" height="${height * 900 / h}" preserveAspectRatio="none"/></g></g></svg>`;
  }

  function selectDocument(id) {
    const index = docs.findIndex(doc => doc.id === id);
    if (index < 0 || !count) return;
    const base = Math.round(T);
    T = base + mod(index - mod(base, count) + count / 2, count) - count / 2;
  }

  function notify(message) {
    const toast = $('.toast');
    toast.querySelector('span').textContent = message;
    toastAnimation?.cancel();
    toastAnimation = toast.animate([
      { transform: 'translate(-50%,140px)' },
      { transform: 'translate(-50%,0)', offset: .12 },
      { transform: 'translate(-50%,0)', offset: .85 },
      { transform: 'translate(-50%,140px)' }
    ], { duration: reduced ? 2500 : 3300, easing: EASE, fill: 'both' });
  }

  function step(direction) {
    if (!isOpen && count) { clearTimeout(wheelTimer); T = Math.round(T) + direction; }
  }

  app = window.createKnowledgeDemo({ showPane, closePane, posterMarkup, selectDocument, documentsChanged: buildShelf, refreshCounter, notify, isPaneOpen: () => isOpen && phase !== 'closing' });
  buildShelf(app.getDocuments());
  document.querySelectorAll('[data-pane]').forEach(button => button.addEventListener('click', () => app.openPane(button.dataset.pane)));
  document.querySelectorAll('.import-shortcut').forEach(button => button.addEventListener('click', () => app.openImport()));
  document.querySelectorAll('[data-open]').forEach(button => button.addEventListener('click', () => app.openPane(button.dataset.open)));
  $('#new-session-shortcut').addEventListener('click', () => app.newSession());
  $('.close').addEventListener('click', closePane);
  $('.sheet-home').addEventListener('click', closePane);
  $('.arrow-prev').addEventListener('click', () => step(-1));
  $('.arrow-next').addEventListener('click', () => step(1));
  play.addEventListener('click', () => { if (currentDoc()) app.readDocument(currentDoc().id); });

  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && isOpen) { event.preventDefault(); closePane(); return; }
    if (isOpen) {
      if (event.key === 'Tab') {
        const controls = [...sheet.querySelectorAll('button,a[href],input,select,textarea,summary,[tabindex="0"]')].filter(element => !element.disabled && !element.closest('[inert]') && element.getClientRects().length);
        const first = controls[0], last = controls.at(-1);
        if (event.shiftKey && (document.activeElement === first || document.activeElement === sheet)) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
      return;
    }
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') { event.preventDefault(); step(event.key === 'ArrowLeft' ? -1 : 1); }
    else if (event.key === 'Enter' && document.activeElement === document.body && currentDoc()) { event.preventDefault(); app.readDocument(currentDoc().id); }
  });
  document.addEventListener('wheel', event => {
    if (isOpen || !count) return;
    event.preventDefault();
    const delta = Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY;
    const units = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? V : 1;
    T += delta * units / (ws + gap) * .5;
    clearTimeout(wheelTimer);
    wheelTimer = setTimeout(() => { T = Math.round(T); }, 160);
  }, { passive: false });
  shelf.addEventListener('pointerdown', event => {
    if (isOpen || !count || event.button !== 0 || drag) return;
    clearTimeout(wheelTimer);
    suppressClick = false;
    drag = { id: event.pointerId, startX: event.clientX, startT: T, moved: false };
  });
  window.addEventListener('pointermove', event => {
    mx = event.clientX;
    my = event.clientY;
    if (pointerQuery.matches && event.pointerType !== 'touch') {
      if (!cursor.classList.contains('seen')) { cx = mx; cy = my; }
      cursor.classList.add('seen');
    }
    overButton = !!event.target.closest('button,a,summary,label') && !event.target.closest('.shelf');
    cursor.classList.toggle('editing', !!event.target.closest('input,textarea,select,.source-resizer'));
    if (!drag || event.pointerId !== drag.id) return;
    const dx = event.clientX - drag.startX;
    if (Math.abs(dx) > 5 && !drag.moved) { drag.moved = true; shelf.setPointerCapture(event.pointerId); }
    if (drag.moved) { T = drag.startT - dx / (ws + gap); suppressClick = true; }
  });
  function finishDrag(event) {
    if (!drag || event.pointerId !== drag.id) return;
    if (drag.moved) T = Math.round(T);
    if (shelf.hasPointerCapture(event.pointerId)) shelf.releasePointerCapture(event.pointerId);
    drag = null;
    setTimeout(() => { suppressClick = false; }, 0);
  }
  window.addEventListener('pointerup', finishDrag);
  window.addEventListener('pointercancel', finishDrag);
  shelf.addEventListener('lostpointercapture', event => { if (drag?.id === event.pointerId) finishDrag(event); });
  document.documentElement.addEventListener('pointerleave', () => { cursor.classList.remove('seen'); hover = -1; overButton = false; });
  window.addEventListener('blur', () => { cursor.classList.remove('seen'); hover = -1; if (drag) { T = Math.round(T); drag = null; } });
  window.addEventListener('resize', measure);
  motionQuery.addEventListener('change', event => { reduced = event.matches; });

  function frame(time) {
    const dt = Math.min(.05, Math.max(0, (time - previousTime) / 1000 || 1 / 60));
    previousTime = time;
    if (count) {
      s += (T - s) * (reduced ? 1 : 1 - Math.exp(-(drag?.moved ? 18 : 5.2) * dt));
      if (Math.abs(T - s) < .0001) s = T;
      if (Math.abs(s) > count * 4) {
        const shift = Math.trunc(s / loop) * loop;
        s -= shift;
        T -= shift;
        rounded -= shift;
        if (drag) drag.startT -= shift;
      }
      const next = Math.round(s);
      if (next !== rounded) {
        const direction = Math.sign(next - rounded);
        if (reduced || Math.abs(next - rounded) > loop) setActive(mod(next, count), direction);
        else for (let i = rounded + direction; direction > 0 ? i <= next : i >= next; i += direction) setActive(mod(i, count), direction);
        rounded = next;
      }
    }
    onCover = count > 0 && !isOpen && !drag?.moved && Math.abs(T - s) < .3 && Math.abs(mx - W / 2) < .55 * d && Math.abs(my - V / 2) < .55 * H;
    const tiltEase = reduced ? 1 : 1 - Math.exp(-7 * dt);
    const targetY = onCover ? (mx - W / 2) / (.55 * d) * 9 : (mx - W / 2) / W * 5;
    const targetX = onCover ? -(my - V / 2) / (H / 2) * 6 : 0;
    tiltY += (targetY - tiltY) * tiltEase;
    tiltX += (targetX - tiltX) * tiltEase;
    if (Math.abs(targetY - tiltY) < .001) tiltY = targetY;
    if (Math.abs(targetX - tiltX) < .001) tiltX = targetX;
    renderTapes(dt);
    const playScale = isOpen || !count ? 0 : smoothstep(Math.max(0, 1 - Math.abs(T - s) * 3));
    play.style.transform = `scale(${playScale})`;
    play.style.pointerEvents = playScale > .2 ? 'auto' : 'none';
    const cursorEase = reduced ? 1 : 1 - Math.exp(-16 * dt);
    cx += (mx - cx) * cursorEase;
    cy += (my - cy) * cursorEase;
    cursor.style.transform = `translate3d(${cx}px,${cy}px,0)`;
    cursor.classList.toggle('open', onCover && !overButton);
    cursor.classList.toggle('spine', !isOpen && !onCover && hover >= 0 && Math.abs(relative(hover)) > .5);
    cursor.classList.toggle('button-hover', overButton && (!onCover || isOpen));
    if (inkEntries.length) {
      inkEntries = inkEntries.filter(({ circle, start }) => {
        if (!reduced && time < start) return true;
        const progress = reduced ? 1 : Math.min(1, (time - start) / 1250);
        circle.setAttribute('r', 720 * (1 - Math.pow(1 - progress, 3)));
        return progress < 1;
      });
    }
    requestAnimationFrame(frame);
  }

  measure();
  renderTapes(1);
  requestAnimationFrame(frame);
  document.fonts.ready.then(measure);
})();
