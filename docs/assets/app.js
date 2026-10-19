/* Plain JavaScript; relative assets work on file:// and GitHub project pages. */
(() => {
  'use strict';
  const data = window.FNC_DOCS;
  const $ = (selector) => document.querySelector(selector);
  const escape = (value) => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const groups = {
    start: {fa:'شروع کار', en:'GET STARTED'},
    account: {fa:'حساب و پیام‌ها', en:'ACCOUNT & MESSAGES'},
    automation: {fa:'رویداد و پایداری', en:'EVENTS & DURABILITY'},
    reference: {fa:'مرجع توسعه‌دهنده', en:'REFERENCE'},
    project: {fa:'دربارهٔ پروژه', en:'PROJECT'},
  };
  const strings = {
    fa: {docs:'مستندات توسعه‌دهندگان', search:'جست‌وجو در مستندات', searchPlaceholder:'متد، مثال یا موضوع…', searchHint:'نام متد یا موضوعی مثل سشن، ارسال یا SQLite را بنویس.', noResults:'نتیجه‌ای پیدا نشد. عبارت کوتاه‌تر یا نام انگلیسی متد را امتحان کن.', copy:'کپی', copied:'کپی شد', copyFailed:'کپی خودکار ممکن نشد؛ متن کد را انتخاب و کپی کن.', onPage:'در این صفحه', previous:'صفحهٔ قبل', next:'ادامهٔ راهنما', class:'کلاس', filter:'نام متد را جست‌وجو کن…', count:'متد', field:'فیلد', type:'نوع', footer:'مستندات نسخهٔ آزمایشی · بررسی‌شده در ۱۷ اکتبر ۲۰۲۶', sidebar:'برای حساب واقعی ایتا', start:'نصب و شروع', api:'مرجع متدها', learn:'راهنما را بخوان', theme:'تغییر حالت روشن و تیره', menu:'فهرست مستندات', close:'بستن', tocNote:'نمونه‌ها به سشن حساب نیاز دارند. ابتدا وارد شو، سپس مقصد را resolve کن.', tocTitle:'یک سشن، یک حساب', unknown:'این صفحه پیدا نشد', unknownText:'از فهرست مستندات یا جست‌وجو به صفحهٔ دلخواه برو.', all:'همهٔ کلاس‌ها', linkSection:'پیوند این بخش', notFound:'متدی مطابق این جست‌وجو نیست.'},
    en: {docs:'Developer documentation', search:'Search documentation', searchPlaceholder:'Method, example or topic…', searchHint:'Search for a method or topic, such as sessions, send or SQLite.', noResults:'No results. Try a shorter phrase or a method name.', copy:'Copy', copied:'Copied', copyFailed:'Automatic copy unavailable. Select and copy the code instead.', onPage:'ON THIS PAGE', previous:'Previous guide', next:'Next guide', class:'Class', filter:'Filter methods…', count:'methods', field:'Field', type:'Type', footer:'Experimental release documentation · Reviewed October 17, 2026', sidebar:'For real Eitaa accounts', start:'Get started', api:'API reference', learn:'Read the guide', theme:'Toggle light and dark theme', menu:'Documentation menu', close:'Close', tocNote:'Examples use an account session. Sign in first, then resolve your destination.', tocTitle:'One session, one account', unknown:'Page not found', unknownText:'Use the documentation navigation or search to find a guide.', all:'All classes', linkSection:'Link to this section', notFound:'No matching methods.'},
  };
  let lang = 'fa';
  let currentPage = null;
  let currentAnchor = '';
  let codeIndex = [];
  let headingObserver = null;
  let toastTimer;
  let apiOwner = 'AccountClient';
  let apiQuery = '';
  let searchTimer;
  const text = value => typeof value === 'object' && value !== null ? value[lang] : String(value ?? '');
  const t = key => strings[lang][key];
  const inline = value => escape(text(value)).replace(/`([^`]+)`/g, '<code class="inline-code" dir="ltr">$1</code>');
  const route = (id, anchor = '') => `#${lang}/${id}${anchor ? '/' + encodeURIComponent(anchor) : ''}`;
  const stored = {get(key) {try {return localStorage.getItem(key);} catch {return null;}}, set(key, value) {try {localStorage.setItem(key, value);} catch {/* Private browsing/file origin may disallow it. */}}};

  function highlight(value, language) {
    if (language !== 'python') return escape(value);
    const pattern = /(#[^\n]*|"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|\b(?:async|await|def|class|from|import|as|with|if|else|elif|for|in|while|try|except|finally|raise|return|pass|not|is|and|or|True|False|None)\b|\b(?:print|len|range|str|int|RuntimeError|Exception)\b|\b\d+\b)/g;
    let result = '', offset = 0;
    for (const match of value.matchAll(pattern)) {
      result += escape(value.slice(offset, match.index));
      const token = match[0];
      const category = token.startsWith('#') ? 'comment' : /^['"]/.test(token) ? 'string' : /^\d/.test(token) ? 'number' : /^(print|len|range|str|int|RuntimeError|Exception)$/.test(token) ? 'builtin' : 'keyword';
      result += `<span class="syntax-${category}">${escape(token)}</span>`;
      offset = match.index + token.length;
    }
    return result + escape(value.slice(offset));
  }

  function codeBlock(value, label = 'Python', language = 'python', compact = false) {
    const index = codeIndex.push(value) - 1;
    return `<div class="code-block">${compact ? '' : `<div class="code-head"><span class="code-dots" aria-hidden="true"><i></i><i></i><i></i></span><span class="code-label">${escape(label)}</span><span class="code-lang">${escape(language)}</span><button class="copy-button" data-copy="${index}" aria-label="${escape(t('copy') + ' ' + label)}">${t('copy')}</button></div>`}<pre tabindex="0"><code>${highlight(value, language)}</code></pre></div>`;
  }

  function table(headers, rows) {
    return `<div class="table-wrap" tabindex="0"><table><thead><tr>${text(headers).map(h => `<th scope="col">${inline(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(row => `<tr>${row.map(cell => `<td>${inline(cell)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
  }

  function block(item) {
    switch (item.type) {
      case 'paragraph': return `<p>${inline(item.text)}</p>`;
      case 'heading': return `<h2 id="${escape(item.id)}">${escape(text(item.text))}<a class="anchor" href="${route(currentPage.id, item.id)}" aria-label="${t('linkSection')}">#</a></h2>`;
      case 'code': return codeBlock(item.value, item.label, item.language);
      case 'note': return `<aside class="note ${item.tone === 'warning' ? 'warning' : ''}"><span aria-hidden="true">${item.tone === 'warning' ? '!' : 'i'}</span><p>${inline(item.text)}</p></aside>`;
      case 'table': return table(item.headers, item.rows);
      case 'api': return `<div class="api-toolbar"><label for="api-owner">${t('class')}</label><select id="api-owner" dir="ltr">${['AccountClient','AccountMedia','AccountProfiles','AccountUpdates','AccountQueue','SQLiteQueueStore','Router','Client','all'].map(owner => `<option value="${owner}" ${apiOwner === owner ? 'selected' : ''}>${owner === 'all' ? t('all') : owner}</option>`).join('')}</select><input id="api-filter" type="search" dir="auto" aria-label="${t('filter')}" placeholder="${t('filter')}" value="${escape(apiQuery)}"></div><div class="api-count" id="api-count" aria-live="polite"></div><div id="api-entries"></div>`;
      case 'models': return data.models.map(model => `<section class="model-section" id="${escape(model.name)}"><h2 class="model-heading">${escape(model.name)}<a class="anchor" href="${route('models', model.name)}" aria-label="${t('linkSection')}">#</a></h2>${table({fa:['فیلد','نوع'],en:['Field','Type']}, model.fields.map(f => ['`' + f.name + '`', '`' + f.type + '`']))}<span class="source-path">${escape(model.source)}</span></section>`).join('');
      case 'resources': return `<div class="resources">${item.links.map(link => `<a class="resource-link" href="${escape(link.path)}"><span>${escape(text(link.label))}</span><span aria-hidden="true">↗</span></a>`).join('')}</div>`;
      default: return '';
    }
  }

  function drawApi() {
    const target = $('#api-entries');
    if (!target) return;
    const query = normalize(apiQuery);
    const methods = data.methods.filter(m => (apiOwner === 'all' || m.owner === apiOwner) && normalize(m.signature + ' ' + text(data.summaries[m.name] || '')).includes(query));
    $('#api-count').textContent = `${methods.length} ${t('count')}`;
    target.innerHTML = methods.length ? methods.map(m => {
      const property = m.property ? 'property' : m.name.startsWith('iter_') ? 'async iterator' : m.asynchronous ? 'async' : 'sync';
      return `<section class="api-entry" id="${m.owner}-${m.name}"><div class="api-title-row"><h3>${escape(m.owner + '.' + m.name)}</h3><span class="api-badge">${property}</span><a class="anchor" href="${route('reference', m.owner + '-' + m.name)}" aria-label="${t('linkSection')}">#</a></div><p class="api-summary">${inline(data.summaries[m.name] || {fa:'جزئیات در راهنمای همین بخش آمده است.',en:'See the corresponding guide for behavior.'})}</p>${codeBlock(m.signature, m.owner + '.' + m.name, 'signature', true)}<span class="source-path">${escape(m.source)}:${m.line}</span></section>`;
    }).join('') : `<p class="search-empty">${t('notFound')}</p>`;
  }

  function navigation() {
    let index = 0;
    $('#navigation').innerHTML = Object.keys(groups).map(group => `<div class="nav-group"><div class="nav-group-title">${text(groups[group])}</div>${data.pages.filter(page => page.group === group).map(page => `<a class="nav-link" href="${route(page.id)}" ${page.id === currentPage?.id ? 'aria-current="page"' : ''}><span class="nav-number" aria-hidden="true">${String(++index).padStart(2,'0')}</span><span>${escape(text(page.title))}</span></a>`).join('')}</div>`).join('');
    document.querySelectorAll('.version').forEach(el => {el.textContent = 'v' + data.version;});
  }

  function heroCards() {
    const cards = [
      {id:'sessions',icon:'{ }',fa:'یک بار وارد شو',en:'Keep your session',faDesc:'سشن را ذخیره کن و دوباره متصل شو.',enDesc:'Sign in, save a session and reconnect.'},
      {id:'messages',icon:'↗',fa:'پیامت را بساز',en:'Make it a message',faDesc:'متن، ریپلای، تاریخچه و عملیات پیام.',enDesc:'Text, replies, history and message actions.'},
      {id:'queue',icon:'≋',fa:'ادامه بده',en:'Keep work durable',faDesc:'رویدادها و عملیات را در SQLite نگه دار.',enDesc:'Persist events and operations in SQLite.'},
    ];
    return `<div class="hero-actions"><a class="primary-button" href="${route('installation')}">${t('start')}<span aria-hidden="true">${lang === 'fa' ? '↙' : '↗'}</span></a><a class="secondary-button" href="${route('reference')}">${t('api')}<span aria-hidden="true">${lang === 'fa' ? '←' : '→'}</span></a></div><div class="intro-cards">${cards.map(card => `<a class="intro-card" href="${route(card.id)}"><span class="card-icon" aria-hidden="true">${card.icon}</span><h3>${escape(card[lang])}</h3><p>${escape(card[lang + 'Desc'])}</p><span class="card-arrow" aria-hidden="true">${lang === 'fa' ? '←' : '→'}</span></a>`).join('')}</div>`;
  }

  function localizeChrome() {
    document.documentElement.lang = lang;
    document.documentElement.dir = lang === 'fa' ? 'rtl' : 'ltr';
    $('#header-label').textContent = t('docs');
    $('#search-label').textContent = t('search');
    $('#search-toggle').setAttribute('aria-label', t('search'));
    $('#search-title').textContent = t('search');
    $('#search-input').placeholder = t('searchPlaceholder');
    $('#language').textContent = lang === 'fa' ? 'EN' : 'فا';
    $('#language').setAttribute('aria-label', lang === 'fa' ? 'Switch to English' : 'تغییر به فارسی');
    $('#theme').setAttribute('aria-label', t('theme'));
    $('#theme').setAttribute('aria-pressed', String(document.documentElement.dataset.theme === 'dark'));
    $('#menu').setAttribute('aria-label', t('menu'));
    $('#sidebar').setAttribute('aria-label', t('menu'));
    $('#page-navigation').setAttribute('aria-label', lang === 'fa' ? 'صفحات قبلی و بعدی' : 'Previous and next guides');
    $('#menu-backdrop').setAttribute('aria-label', t('close'));
    $('#sidebar-note').textContent = t('sidebar');
    $('#footer-note').textContent = t('footer');
    $('#toc-label').textContent = t('onPage');
    $('#toc-note').innerHTML = `<strong>${t('tocTitle')}</strong>${t('tocNote')}`;
    $('.brand').href = route('overview');
  }

  function drawToc() {
    const headings = [...$('#article').querySelectorAll('h2[id], section.model-section[id]')];
    $('#toc').innerHTML = headings.map(h => `<a class="toc-link" href="${route(currentPage.id, h.id)}">${escape(h.querySelector('h2')?.textContent.replace(/#$/, '') || h.textContent.replace(/#$/, ''))}</a>`).join('');
    if (headingObserver) headingObserver.disconnect();
    headingObserver = new IntersectionObserver(entries => {
      const visible = entries.filter(entry => entry.isIntersecting);
      if (!visible.length) return;
      const id = visible[0].target.id;
      $('#toc').querySelectorAll('a').forEach(a => a.classList.toggle('active', a.hash === route(currentPage.id, id)));
    }, {rootMargin:'-90px 0px -60% 0px'});
    headings.forEach(h => headingObserver.observe(h));
  }

  function setMenu(open) {
    document.body.classList.toggle('menu-open', open);
    $('#menu').setAttribute('aria-expanded', String(open));
    $('#menu-backdrop').hidden = !open;
    $('#sidebar').inert = matchMedia('(max-width: 800px)').matches && !open;
  }

  function render() {
    const parts = location.hash.slice(1).split('/');
    const nextLang = parts[0] === 'en' ? 'en' : 'fa';
    const nextId = parts[1] || 'overview';
    let anchor;
    try {anchor = decodeURIComponent(parts.slice(2).join('/'));} catch {anchor = '';}
    const nextPage = data.pages.find(p => p.id === nextId);
    const samePage = currentPage?.id === nextId && lang === nextLang;
    lang = nextLang;
    currentAnchor = anchor;
    stored.set('fnceitaa-docs-language', lang);
    setMenu(false);
    localizeChrome();
    if (!nextPage) {
      currentPage = null;
      navigation();
      $('#article').innerHTML = `<h1>${t('unknown')}</h1><p>${t('unknownText')}</p><a class="primary-button" href="${route('overview')}">${t('start')}</a>`;
      $('#page-navigation').innerHTML = $('#toc').innerHTML = $('#breadcrumb').innerHTML = '';
      document.title = t('unknown') + ' — Fnc Eitaa';
      window.scrollTo(0,0);
      return;
    }
    currentPage = nextPage;
    if (!samePage) {
      if (nextId === 'reference' && anchor.includes('-')) {
        apiOwner = anchor.split('-')[0]; apiQuery = '';
      }
      codeIndex = [];
      $('#breadcrumb').innerHTML = `<span>${lang === 'fa' ? 'مستندات' : 'Documentation'}</span><span class="crumb-divider">/</span><span>${text(groups[currentPage.group])}</span>`;
      $('#article').innerHTML = `<div class="page-kicker">FNC EITAA / ${escape(currentPage.id.toUpperCase())}</div><h1>${escape(text(currentPage.title))}</h1><p class="page-description">${escape(text(currentPage.description))}</p><div class="meta-row"><span class="pill">Python 3.10+</span><span class="pill">async / await</span><span class="pill experimental">v${data.version} · experimental</span></div>${currentPage.id === 'overview' ? heroCards() : ''}<div class="guide-body">${currentPage.blocks.map(block).join('')}</div>`;
      const index = data.pages.indexOf(currentPage), previous = data.pages[index-1], next = data.pages[index+1];
      $('#page-navigation').innerHTML = (previous ? `<a class="page-nav-card" href="${route(previous.id)}"><small>${t('previous')}</small>${escape(text(previous.title))}</a>` : '<span></span>') + (next ? `<a class="page-nav-card next" href="${route(next.id)}"><small>${t('next')}</small>${escape(text(next.title))} ${lang === 'fa' ? '←' : '→'}</a>` : '');
      navigation();
      drawApi();
      drawToc();
      document.title = `${text(currentPage.title)} — Fnc Eitaa`;
    } else if (nextId === 'reference' && anchor.includes('-')) {
      apiOwner = anchor.split('-')[0]; apiQuery = '';
      $('#api-owner').value = apiOwner; $('#api-filter').value = ''; drawApi();
    }
    requestAnimationFrame(() => {
      if (anchor) document.getElementById(anchor)?.scrollIntoView({behavior:'instant', block:'start'});
      else if (!samePage) window.scrollTo({top:0,behavior:'instant'});
    });
  }

  function normalize(value) {
    return String(value).toLowerCase().replace(/[يى]/g,'ی').replace(/ك/g,'ک').replace(/[\u200c\u200e\u200f]/g,' ').replace(/\s+/g,' ').trim();
  }

  function pageSearchText(page) {
    const parts = [text(page.title), text(page.description)];
    page.blocks.forEach(b => {
      if (b.text) parts.push(text(b.text));
      if (b.value) parts.push(b.value);
      if (b.rows) b.rows.forEach(row => row.forEach(cell => parts.push(text(cell))));
    });
    return normalize(parts.join(' '));
  }

  function search() {
    const query = normalize($('#search-input').value);
    if (!query) {$('#search-results').innerHTML = `<p class="search-empty">${t('searchHint')}</p>`;return;}
    const tokens = query.split(' ');
    const results = [];
    data.pages.forEach(page => {
      if (tokens.every(token => pageSearchText(page).includes(token))) results.push({title:text(page.title),description:text(page.description),href:route(page.id),category:text(groups[page.group]),score:normalize(text(page.title)).includes(query)?3:1});
    });
    data.methods.forEach(method => {
      const summary = text(data.summaries[method.name] || '');
      if (tokens.every(token => normalize(method.signature + ' ' + summary).includes(token))) results.push({title:method.owner + '.' + method.name,description:summary,href:route('reference',method.owner + '-' + method.name),category:'API',score:normalize(method.name).includes(query)?4:2});
    });
    data.models.forEach(model => {
      if (tokens.every(token => normalize(model.name + ' ' + model.fields.map(f => f.name).join(' ')).includes(token))) results.push({title:model.name,description:model.fields.map(f => f.name).slice(0,6).join(' · '),href:route('models', model.name),category:lang==='fa'?'مدل':'Model',score:2});
    });
    results.sort((a,b) => b.score - a.score);
    $('#search-results').innerHTML = results.length ? results.slice(0,30).map(result => `<a class="search-result" href="${result.href}"><small>${escape(result.category)}</small><strong dir="auto">${escape(result.title)}</strong><p dir="auto">${escape(result.description)}</p></a>`).join('') : `<p class="search-empty">${t('noResults')}</p>`;
  }

  function openSearch() {
    if ($('#search-dialog').open) return;
    $('#search-dialog').showModal();
    $('#search-input').value = ''; search(); $('#search-input').focus();
  }

  function toast(value) {
    clearTimeout(toastTimer); $('#toast').textContent = value; $('#toast').classList.add('visible');
    toastTimer = setTimeout(() => $('#toast').classList.remove('visible'), 2200);
  }

  $('#search-toggle').addEventListener('click', openSearch);
  $('#search-input').addEventListener('input', () => {clearTimeout(searchTimer); searchTimer = setTimeout(search,100);});
  $('#search-input').addEventListener('keydown', event => {if(event.key==='ArrowDown') {event.preventDefault();$('#search-results a')?.focus();}});
  $('#search-results').addEventListener('click', event => {
    const link = event.target.closest('a');
    if (link) {
      $('#search-dialog').close();
      if (link.hash === location.hash) render();
    }
  });
  $('#search-dialog').addEventListener('click', event => {if(event.target === $('#search-dialog')) $('#search-dialog').close();});
  $('#language').addEventListener('click', () => {location.hash = `#${lang === 'fa' ? 'en' : 'fa'}/${currentPage?.id || 'overview'}${currentAnchor ? '/' + encodeURIComponent(currentAnchor) : ''}`;});
  $('#theme').addEventListener('click', () => {
    const theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = theme; stored.set('fnceitaa-docs-theme', theme);
    $('#theme').setAttribute('aria-pressed', String(theme === 'dark'));
  });
  $('#menu').addEventListener('click', () => setMenu(!document.body.classList.contains('menu-open')));
  $('#menu-backdrop').addEventListener('click', () => setMenu(false));
  $('#article').addEventListener('change', event => {if(event.target.id === 'api-owner') {apiOwner=event.target.value;drawApi();}});
  $('#article').addEventListener('input', event => {if(event.target.id === 'api-filter') {apiQuery=event.target.value;drawApi();}});
  $('#article').addEventListener('click', async event => {
    const button = event.target.closest('[data-copy]');
    if (!button) return;
    try {await navigator.clipboard.writeText(codeIndex[Number(button.dataset.copy)]); toast(t('copied'));}
    catch {toast(t('copyFailed'));}
  });
  $('.skip').addEventListener('click', event => {event.preventDefault();$('#main').focus();});
  document.addEventListener('keydown', event => {
    const editing = /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName) || event.target.isContentEditable;
    if ((event.key === '/' && !editing) || ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k')) {event.preventDefault();openSearch();}
    if(event.key === 'Escape' && document.body.classList.contains('menu-open')) {setMenu(false);$('#menu').focus();}
  });
  const theme = stored.get('fnceitaa-docs-theme');
  document.documentElement.dataset.theme = theme === 'dark' || theme === 'light' ? theme : 'light';
  window.addEventListener('hashchange', render);
  matchMedia('(max-width: 800px)').addEventListener('change', () => setMenu(false));
  if (!location.hash || location.hash === '#') history.replaceState(null,'',`#${stored.get('fnceitaa-docs-language') === 'en' ? 'en' : 'fa'}/overview`);
  render();
})();
