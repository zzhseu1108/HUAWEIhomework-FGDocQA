(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const on = (id, type, handler) => $(id)?.addEventListener(type, handler);
  const text = (id, value) => { if ($(id)) $(id).textContent = value; };
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
  const modelModeLabel = (mode) => ({ mock: "Mock", http: "HTTP", mixed: "HTTP + 本地" }[mode] || "未知模式");
  const uid = () => globalThis.crypto?.randomUUID?.() || Date.now().toString(36) + Math.random().toString(36).slice(2);
  const icons = {
    copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M15 9V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h4"/>',
    bookmark: '<path d="M6 4h12v17l-6-4-6 4V4Z"/>',
    trash: '<path d="M3 6h18M9 6V3h6v3M6 6l1 15h10l1-15M10 10v7m4-7v7"/>',
    arrow: '<path d="M5 12h14m-6-6 6 6-6 6"/>',
    file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><path d="M14 2v6h6M8 13h8M8 17h5"/>',
    retry: '<path d="M3 10a9 9 0 1 1 2 8M3 4v6h6"/>',
  };
  const icon = (name) => `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name] || icons.file}</svg>`;
  const STORAGE_KEY = "docqa.ocean.workspace.v1";
  const state = { sessions: [], saved: [], activeId: null, view: "chat", docs: [], stats: null, busy: false, pendingId: null, libraryRequest: 0, docRequest: 0, storageFailed: false, drawerReturn: null };

  function toast(message) {
    const element = $("toast");
    if (!element) return;
    element.textContent = message;
    element.classList.add("show");
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => element.classList.remove("show"), 2800);
  }
  function persist() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ sessions: state.sessions, saved: state.saved, activeId: state.activeId }));
    } catch (_) {
      if (!state.storageFailed) toast("浏览器存储不可用，本次记录暂存于当前页面。");
      state.storageFailed = true;
    }
  }
  function cleanAnswer(value) {
    if (!value || typeof value !== "object" || typeof value.answer !== "string") return null;
    return {
      ...value,
      citations: (Array.isArray(value.citations) ? value.citations : []).filter((entry) => entry && typeof entry === "object").slice(0, 30),
      trace: (Array.isArray(value.trace) ? value.trace : []).filter((entry) => entry && typeof entry === "object").slice(0, 50),
      warnings: (Array.isArray(value.warnings) ? value.warnings : []).map(String).slice(0, 50),
    };
  }
  function restore() {
    try {
      const value = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
      if (!value || typeof value !== "object") return;
      state.sessions = (Array.isArray(value.sessions) ? value.sessions : []).filter((item) => item && typeof item.id === "string" && Array.isArray(item.messages)).slice(0, 30).map((item) => ({
        id: item.id, title: String(item.title || "新对话"), category: String(item.category || ""), updatedAt: Number(item.updatedAt) || Date.now(),
        messages: item.messages.filter((message) => message && ["user", "assistant", "error"].includes(message.role) && typeof message.id === "string" && (message.role !== "assistant" || cleanAnswer(message.data))).slice(-100).map((message) => ({ ...message, content: String(message.content || ""), question: String(message.question || ""), category: String(message.category || ""), data: message.role === "assistant" ? cleanAnswer(message.data) : undefined }))
      }));
      state.saved = (Array.isArray(value.saved) ? value.saved : []).filter((item) => item && typeof item.id === "string" && cleanAnswer(item.data)).slice(0, 100).map((item) => ({ ...item, question: String(item.question || "文档问答"), data: cleanAnswer(item.data) }));
      state.activeId = state.sessions.some((item) => item.id === value.activeId) ? value.activeId : null;
    } catch (_) { /* An invalid or unavailable browser store must not block the app. */ }
  }
  async function api(path, options = {}) {
    const response = await fetch(path, options);
    let data;
    try { data = await response.json(); } catch (_) { throw new Error("服务暂时没有返回有效内容，请稍后重试。"); }
    if (!response.ok) throw new Error(data.error || `请求失败（${response.status}）`);
    return data;
  }

  // All document text is escaped before rendering. Inline placeholders prevent
  // links/code labels from being interpreted again as markup.
  function inline(source) {
    const fragments = [];
    const keep = (html) => { fragments.push(html); return `\u0001${fragments.length - 1}\u0001`; };
    let value = String(source).replace(/\u0001/g, "");
    value = value.replace(/`([^`]+)`/g, (_, code) => keep(`<code>${esc(code)}</code>`));
    value = value.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, label, url) => {
      if (!/^(https?:\/\/|file:\/\/)/i.test(url)) return label;
      return keep(`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>`);
    });
    value = esc(value).replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    return value.replace(/\u0001(\d+)\u0001/g, (_, index) => fragments[Number(index)] || "");
  }
  function markdown(source, options = {}) {
    if (!source) return "";
    const blocks = [];
    let value = String(source).replace(/\r\n/g, "\n").replace(/\u0000/g, "");
    value = value.replace(/```([\w-]*)\n([\s\S]*?)```/g, (_, lang, code) => { blocks.push({ lang: lang.toLowerCase(), code }); return `\u0000B${blocks.length - 1}\u0000`; });
    const lines = value.split("\n");
    let html = "", list = "", table = [];
    const closeList = () => { if (list) { html += `</${list}>`; list = ""; } };
    const flushTable = () => {
      if (!table.length) return;
      const rows = table.filter((row) => !row.every((cell) => /^:?-{2,}:?$/.test(cell.trim())));
      if (rows.length) html += '<div class="table-wrap"><table><thead><tr>' + rows[0].map((cell) => `<th>${inline(cell.trim())}</th>`).join("") + "</tr></thead><tbody>" + rows.slice(1).map((row) => "<tr>" + row.map((cell) => `<td>${inline(cell.trim())}</td>`).join("") + "</tr>").join("") + "</tbody></table></div>";
      table = [];
    };
    for (const line of lines) {
      if (/^\s*\|.*\|\s*$/.test(line)) { closeList(); table.push(line.trim().slice(1, -1).split("|")); continue; }
      flushTable();
      const block = line.match(/^\u0000B(\d+)\u0000\s*$/);
      if (block) {
        closeList();
        const item = blocks[Number(block[1])];
        const svg = item.lang === "mermaid" ? renderMermaid(item.code) : "";
        html += svg ? `<div class="mermaid-wrap">${svg}</div>` : `<pre><code>${esc(item.code)}</code></pre>`;
        continue;
      }
      const image = line.match(/^\s*!\[([^\]]*)\]\(([^)]+)\)\s*$/);
      if (image && options.images) {
        closeList();
        const item = options.images.find((entry) => entry.path === image[2]);
        if (item?.url && /^\/api\/asset\?/.test(item.url)) html += `<figure class="reader-image"><a href="${esc(item.url)}" target="_blank" rel="noopener" title="打开原图"><img src="${esc(item.url)}" alt="${esc(item.title || image[1])}" loading="lazy"></a><figcaption class="reader-caption">${esc(item.title || image[1])} · 第 ${Number(item.page) || 1} 页 · 图 ${Number(item.figure_index) || 1}</figcaption></figure>`;
        else html += `<p class="reader-caption">${esc(image[1])} · 暂无可预览原图</p>`;
        continue;
      }
      if (/^\s*<!--/.test(line)) continue;
      const heading = line.match(/^(#{1,6})\s+(.+)$/);
      if (heading) { closeList(); const level = Math.min(4, heading[1].length); html += `<h${level}>${inline(heading[2])}</h${level}>`; continue; }
      if (/^\s*(---|\*\*\*|___)\s*$/.test(line)) { closeList(); html += "<hr>"; continue; }
      if (/^\s*>\s?/.test(line)) { closeList(); html += `<blockquote>${inline(line.replace(/^\s*>\s?/, ""))}</blockquote>`; continue; }
      const unordered = line.match(/^\s*[-*]\s+(.+)$/), ordered = line.match(/^\s*\d+\.\s+(.+)$/);
      if (unordered || ordered) { const type = unordered ? "ul" : "ol"; if (list !== type) { closeList(); html += `<${type}>`; list = type; } html += `<li>${inline((unordered || ordered)[1])}</li>`; continue; }
      if (!line.trim()) { closeList(); continue; }
      closeList(); html += `<p>${inline(line)}</p>`;
    }
    closeList(); flushTable(); return html;
  }
  function renderMermaid(source) {
    try {
      const raw = String(source).replace(/\r\n/g, "\n").split("\n").map((line) => line.trim()).filter(Boolean);
      if (!raw.length) return "";
      let direction = "LR";
      if (/^(flowchart|graph)\s+(TD|TB)/i.test(raw[0])) direction = "TD";
      if (!/^(flowchart|graph)\s+(LR|TD|TB)/i.test(raw[0])) return "";
      const nodes = new Map();
      const edges = [];
      const parseNode = (token) => {
        token = token.trim();
        const match = token.match(/^([A-Za-z0-9_]+)\s*(?:\[([^\]]*)\]|\(([^)]*)\)|\{([^}]*)\})?$/);
        if (!match) return null;
        const id = match[1];
        const label = (match[2] ?? match[3] ?? match[4] ?? id).trim();
        if (!nodes.has(id)) nodes.set(id, { id, label: label || id });
        return id;
      };
      for (const line of raw.slice(1)) {
        if (!/-->|-.->|==>/.test(line)) continue;
        const labelMatch = line.match(/\|([^|]*)\|/);
        const edgeLabel = labelMatch ? labelMatch[1].trim() : "";
        const cleaned = line.replace(/\|[^|]*\|/g, "");
        const parts = cleaned.split(/-->|-.->|==>/);
        if (parts.length < 2) continue;
        for (let index = 0; index < parts.length - 1; index++) {
          const from = parseNode(parts[index]);
          const to = parseNode(parts[index + 1]);
          if (from && to) edges.push({ from, to, label: edgeLabel });
        }
      }
      if (nodes.size < 2 || nodes.size > 16 || !edges.length) return "";

      const inDegree = new Map([...nodes.keys()].map((id) => [id, 0]));
      const outgoing = new Map([...nodes.keys()].map((id) => [id, []]));
      edges.forEach((edge) => {
        inDegree.set(edge.to, (inDegree.get(edge.to) || 0) + 1);
        outgoing.get(edge.from).push(edge.to);
      });
      const level = new Map();
      const queue = [...nodes.keys()].filter((id) => (inDegree.get(id) || 0) === 0);
      queue.forEach((id) => level.set(id, 0));
      // Kahn traversal processes each node once; cyclic diagrams fall back to source.
      let visited = 0;
      for (let index = 0; index < queue.length; index++) {
        const id = queue[index];
        visited++;
        for (const next of outgoing.get(id) || []) {
          level.set(next, Math.max(level.get(next) || 0, (level.get(id) || 0) + 1));
          inDegree.set(next, inDegree.get(next) - 1);
          if (inDegree.get(next) === 0) queue.push(next);
        }
      }
      if (visited !== nodes.size) return "";
      const levels = [];
      nodes.forEach((node) => {
        const index = level.get(node.id) || 0;
        if (!levels[index]) levels[index] = [];
        levels[index].push(node);
      });
      const nodeW = 112, nodeH = 44;
      const gapMain = direction === "LR" ? 30 : 42;
      const gapCross = direction === "LR" ? 22 : 54;
      const counts = Array.from({ length: levels.length }, (_, index) => (levels[index] ? levels[index].length : 0));
      const maxCount = Math.max(...counts, 0);
      if (!maxCount) return "";
      const cols = direction === "LR" ? levels.length : maxCount;
      const rows = direction === "LR" ? maxCount : levels.length;
      const width = 60 + cols * nodeW + Math.max(0, cols - 1) * gapMain;
      const height = 50 + rows * nodeH + Math.max(0, rows - 1) * gapCross;
      const positions = new Map();
      levels.forEach((group, levelIndex) => {
        if (!group) return;
        group.forEach((node, index) => {
          let x, y;
          if (direction === "LR") {
            x = 30 + levelIndex * (nodeW + gapMain);
            y = 24 + index * (nodeH + gapCross) + ((maxCount - group.length) * (nodeH + gapCross)) / 2;
          } else {
            x = 30 + index * (nodeW + gapMain) + ((maxCount - group.length) * (nodeW + gapMain)) / 2;
            y = 24 + levelIndex * (nodeH + gapCross);
          }
          x += nodeW / 2;
          y += nodeH / 2;
          positions.set(node.id, { x, y });
        });
      });
      const palette = ["#eaf4fc", "#e9f7f4", "#fdf4e8", "#f0eefb"];
      const stroke = ["#2f7fd1", "#0f8f84", "#c9832d", "#6b5bb5"];
      const markerId = "arrow-" + Math.random().toString(36).slice(2, 8);
      let svg = `<svg viewBox="0 0 ${width} ${height}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Mermaid 流程图">`;
      svg += `<defs><marker id="${markerId}" markerWidth="9" markerHeight="9" refX="7" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7 Z" fill="#7fa6c4"/></marker></defs>`;
      edges.forEach((edge) => {
        const from = positions.get(edge.from), to = positions.get(edge.to);
        if (!from || !to) return;
        let x1, y1, x2, y2, path;
        if (direction === "LR") {
          x1 = from.x + nodeW / 2; y1 = from.y;
          x2 = to.x - nodeW / 2; y2 = to.y;
          if (Math.abs(y1 - y2) < 2) path = `M${x1},${y1} L${x2 - 5},${y2}`;
          else path = `M${x1},${y1} C${x1 + 42},${y1} ${x2 - 42},${y2} ${x2 - 5},${y2}`;
        } else {
          x1 = from.x; y1 = from.y + nodeH / 2;
          x2 = to.x; y2 = to.y - nodeH / 2;
          if (Math.abs(x1 - x2) < 2) path = `M${x1},${y1} L${x2},${y2 - 5}`;
          else path = `M${x1},${y1} C${x1},${y1 + 34} ${x2},${y2 - 34} ${x2},${y2 - 5}`;
        }
        svg += `<path d="${path}" fill="none" stroke="#7fa6c4" stroke-width="1.5" marker-end="url(#${markerId})"/>`;
        if (edge.label) {
          const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
          svg += `<rect x="${mx - 16}" y="${my - 10}" width="32" height="18" rx="5" fill="#fff" stroke="#dbe8f2"/>`;
          svg += `<text x="${mx}" y="${my + 3}" text-anchor="middle" font-size="10" fill="#698399">${esc(edge.label)}</text>`;
        }
      });
      nodes.forEach((node) => {
        const pos = positions.get(node.id);
        const colorIndex = (level.get(node.id) || 0) % palette.length;
        const words = String(node.label).split(/\s+/);
        let lines = [node.label];
        if (node.label.length > 12) {
          const mid = Math.ceil(node.label.length / 2);
          lines = [node.label.slice(0, mid), node.label.slice(mid)];
        } else if (words.length > 1 && node.label.length > 10) {
          lines = [words.slice(0, Math.ceil(words.length / 2)).join(" "), words.slice(Math.ceil(words.length / 2)).join(" ")];
        }
        lines = lines.slice(0, 2);
        svg += `<rect x="${pos.x - nodeW / 2}" y="${pos.y - nodeH / 2}" width="${nodeW}" height="${nodeH}" rx="9" fill="${palette[colorIndex]}" stroke="${stroke[colorIndex]}" stroke-width="1.4"/>`;
        const baseY = pos.y - (lines.length - 1) * 8 + 4;
        lines.forEach((line, index) => {
          svg += `<text x="${pos.x}" y="${baseY + index * 16}" text-anchor="middle" font-size="13" font-family="PingFang SC, sans-serif" fill="#1c4568">${esc(line)}</text>`;
        });
      });
      svg += "</svg>";
      return svg;
    } catch (error) {
      return "";
    }
  }

  const currentSession = () => state.sessions.find((item) => item.id === state.activeId);
  const relativeDate = (value) => {
    const date = new Date(Number(value) || Date.now());
    return date.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
  };
  function ensureSession() {
    let session = currentSession();
    if (!session) {
      session = { id: uid(), title: "新对话", messages: [], category: $("categorySelect")?.value || "", updatedAt: Date.now() };
      state.sessions.unshift(session); state.sessions = state.sessions.slice(0, 30); state.activeId = session.id;
    }
    return session;
  }
  function renderHistory() {
    if (!$("historyList")) return;
    $("historyList").innerHTML = state.sessions.map((session) => `<div class="history-item ${session.id === state.activeId ? "active" : ""}"><button class="history-open" data-session="${esc(session.id)}" title="${esc(session.title)}"><span>${esc(session.title)}</span><small>${relativeDate(session.updatedAt)}</small></button><button class="history-delete" data-delete-session="${esc(session.id)}" aria-label="删除会话：${esc(session.title)}" title="删除会话">${icon("trash")}</button></div>`).join("") || '<p class="history-empty">在这里，留住每一次发现。</p>';
  }
  function setView(view) {
    state.view = ["chat", "library", "saved"].includes(view) ? view : "chat";
    ["chat", "library", "saved"].forEach((name) => { if ($(name + "View")) $(name + "View").hidden = name !== state.view; });
    document.querySelectorAll("[data-view]").forEach((button) => { button.classList.toggle("active", button.dataset.view === state.view); button.setAttribute("aria-current", button.dataset.view === state.view ? "page" : "false"); });
    if ($("exportChat")) $("exportChat").hidden = state.view !== "chat";
    text("sessionTitle", state.view === "library" ? "知识文库" : state.view === "saved" ? "我的收藏" : currentSession()?.title || "FGDocQA · 文档问答");
    closeSidebar();
    if (state.view === "saved") renderSaved();
    if (state.view === "library") renderLibrary();
    if (state.view === "chat" && !currentSession()?.messages.length) requestAnimationFrame(() => { if ($("chat")) $("chat").scrollTop = 0; });
  }
  function closeSidebar() { $("sidebar")?.classList.remove("open"); $("sidebarOverlay")?.classList.remove("open"); $("mobileMenu")?.setAttribute("aria-expanded", "false"); }
  function newChat() {
    state.activeId = null; persist(); renderHistory(); renderChat(); setView("chat");
    if ($("categorySelect")) $("categorySelect").value = "";
    $("input")?.focus({ preventScroll: true });
    requestAnimationFrame(() => { if ($("chat")) $("chat").scrollTop = 0; });
  }
  function scrollBottom() { if ($("chat")) requestAnimationFrame(() => { $("chat").scrollTop = $("chat").scrollHeight; }); }
  function messageShell(role) {
    const wrap = document.createElement("article"); wrap.className = `msg ${role}`;
    const avatar = document.createElement("div"); avatar.className = "avatar";
    if (role === "user") avatar.textContent = "我";
    else avatar.innerHTML = '<img src="/static/whale-guide.png" alt="DeepSeek 鲸鱼娘">';
    const bubble = document.createElement("div"); bubble.className = "bubble";
    wrap.append(avatar, bubble); return { wrap, bubble };
  }
  function answerMarkup(data, id, saved = false) {
    data = cleanAnswer(data) || { answer: "", citations: [], trace: [] };
    const citations = Array.isArray(data.citations) ? data.citations : [];
    const trace = Array.isArray(data.trace) ? data.trace : [];
    const isSaved = state.saved.some((item) => item.id === id);
    let html = `<div class="answer-head"><b>FGDocQA</b><span>文档助手</span><span class="${data.sufficient ? "ok" : "warn"}">${data.sufficient ? "依据已找到" : "暂无充分依据"}</span></div><div class="answer">${markdown(data.answer)}</div>`;
    if (citations.length) html += '<div class="citations">' + citations.map((citation) => `<button class="citation" data-doc="${esc(citation.doc_id)}" data-page="${Number(citation.page) || 1}" title="${esc(citation.snippet)}"><span class="cite-ref">${Number(citation.ref) || 1}</span><span class="cite-main"><b>${esc(citation.doc_name)}</b><span>${esc(citation.figure || `第 ${Number(citation.page) || 1} 页`)}</span></span>${icon("arrow")}</button>`).join("") + '</div>';
    if (trace.length) html += '<details class="trace"><summary>查看检索过程</summary><div class="trace-grid">' + trace.map((step, index) => {
      const label = step.action === "retrieve" ? `第 ${Number(step.round) || index + 1} 轮检索` : step.action === "synthesize" ? "整理回答" : "证据判断";
      const detail = step.action === "retrieve" ? `命中 ${Number(step.hits) || 0} 个片段 · 覆盖率 ${Math.round((Number(step.coverage) || 0) * 100)}%` : step.action === "synthesize" ? `模型 ${step.model || ""}` : step.reason || "";
      return `<div class="trace-row"><em>${label}</em><span>${esc(detail)}</span></div>`;
    }).join("") + '</div></details>';
    if (Array.isArray(data.warnings) && data.warnings.length) html += `<details class="trace"><summary>运行提示 · ${data.warnings.length}</summary><div class="trace-grid">${data.warnings.map((warning) => `<p>${esc(warning)}</p>`).join("")}</div></details>`;
    html += `<div class="answer-actions"><div class="meta-line"><span>${Number(data.rounds_used) || 1} 轮检索</span><span>${citations.length} 条引用</span><span>${(Number(data.elapsed_ms) || 0) < 1000 ? Math.round(Number(data.elapsed_ms) || 0) + " ms" : ((Number(data.elapsed_ms) || 0) / 1000).toFixed(1) + " s"}</span><span>${modelModeLabel(data.model_mode)}</span></div><button class="action-btn" data-copy="${esc(id)}" title="复制回答" aria-label="复制回答">${icon("copy")}<span>复制</span></button><button class="action-btn ${isSaved ? "active" : ""}" data-save="${esc(id)}" aria-pressed="${isSaved}" title="${isSaved ? "取消收藏" : "收藏回答"}">${icon("bookmark")}<span>${isSaved ? "已收藏" : "收藏"}</span></button>${saved ? `<button class="action-btn" data-export-answer="${esc(id)}" title="导出回答">导出</button>` : ""}</div>`;
    return html;
  }
  function renderChat() {
    const session = currentSession();
    if ($("messages")) $("messages").innerHTML = "";
    if ($("welcome")) $("welcome").hidden = !!session?.messages.length;
    text("sessionTitle", session?.title || "FGDocQA · 文档问答");
    if ($("exportChat")) $("exportChat").disabled = !session?.messages.some((item) => item.role === "assistant");
    if ($("categorySelect") && session) $("categorySelect").value = session.category || "";
    for (const message of session?.messages || []) {
      const { wrap, bubble } = messageShell(message.role === "user" ? "user" : "assistant");
      wrap.dataset.message = message.id;
      if (message.role === "user") bubble.textContent = message.content || "";
      else if (message.role === "assistant") bubble.innerHTML = answerMarkup(message.data || {}, message.id);
      else bubble.innerHTML = `<div class="error-box"><b>这次没能完成检索</b><p>${esc(message.content)}</p><button class="action-btn" data-retry="${esc(message.id)}">${icon("retry")}重新尝试</button></div>`;
      $("messages")?.append(wrap);
    }
    if (state.busy && session?.id === state.pendingId) {
      const { wrap, bubble } = messageShell("assistant"); wrap.classList.add("loading-message");
      bubble.innerHTML = '<div class="thinking"><span class="dots"><i></i><i></i><i></i></span>正在潜入文库，寻找答案的依据…</div>';
      $("messages")?.append(wrap);
    }
    if ($("send")) $("send").disabled = state.busy;
    if (session?.messages.length || (state.busy && session?.id === state.pendingId)) scrollBottom();
    else requestAnimationFrame(() => { if ($("chat")) $("chat").scrollTop = 0; });
  }
  async function ask(rawQuestion, retryMessage = null) {
    const question = String(rawQuestion || "").trim();
    if (!question || state.busy) return;
    if (question.length > 2000) { toast("问题最多支持 2000 个字符，请精简后再试。"); return; }
    const session = ensureSession();
    const sessionId = session.id;
    const category = retryMessage ? retryMessage.category || "" : $("categorySelect")?.value || "";
    session.category = category;
    if (retryMessage) session.messages = session.messages.filter((item) => item.id !== retryMessage.id);
    else session.messages.push({ id: uid(), role: "user", content: question, createdAt: Date.now() });
    if (session.title === "新对话") session.title = question.slice(0, 26);
    session.updatedAt = Date.now();
    state.sessions = [session, ...state.sessions.filter((item) => item.id !== session.id)].slice(0, 30);
    state.busy = true; state.pendingId = sessionId;
    if ($("input")) $("input").value = "";
    resizeInput(); persist(); setView("chat"); renderHistory(); renderChat();
    try {
      const data = await api("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question, category: category || null, top_k: 6, max_rounds: 2 }) });
      const target = state.sessions.find((item) => item.id === sessionId);
      if (target) { target.messages.push({ id: uid(), role: "assistant", data, question, category, createdAt: Date.now() }); target.updatedAt = Date.now(); }
      if (target && state.activeId !== sessionId) toast(`「${target.title}」的回答已完成。`);
    } catch (error) {
      const target = state.sessions.find((item) => item.id === sessionId);
      if (target) target.messages.push({ id: uid(), role: "error", content: error.message, question, category, createdAt: Date.now() });
    } finally {
      state.busy = false; state.pendingId = null; persist(); renderHistory();
      if (state.activeId === sessionId && state.view === "chat") renderChat();
      else if ($("send")) $("send").disabled = false;
      if (state.activeId === sessionId && state.view === "chat") $("input")?.focus();
    }
  }
  function findMessage(id) { return state.sessions.flatMap((session) => session.messages).find((message) => message.id === id) || state.saved.find((message) => message.id === id); }
  function updateSaveButtons() {
    document.querySelectorAll("[data-save]").forEach((button) => {
      const saved = state.saved.some((item) => item.id === button.dataset.save);
      button.classList.toggle("active", saved); button.setAttribute("aria-pressed", String(saved)); button.title = saved ? "取消收藏" : "收藏回答";
      const span = button.querySelector("span"); if (span) span.textContent = saved ? "已收藏" : "收藏";
    });
    text("savedCount", state.saved.length || "0");
  }
  function toggleSave(id) {
    const existing = state.saved.some((item) => item.id === id);
    if (existing) state.saved = state.saved.filter((item) => item.id !== id);
    else {
      const message = findMessage(id);
      if (!message?.data) return;
      state.saved.unshift({ id, question: message.question || message.data.question || "文档问答", data: message.data, createdAt: Date.now() });
      state.saved = state.saved.slice(0, 100);
    }
    persist(); updateSaveButtons(); if (state.view === "saved") renderSaved(); toast(existing ? "已移出收藏" : "已收藏，随时回来重读。");
  }
  function renderSaved() {
    if (!$("savedView")) return;
    const container = $("savedList") || $("savedView").querySelector(".saved-list") || $("savedView");
    container.innerHTML = state.saved.length ? state.saved.map((item) => `<article class="saved-card"><div class="saved-meta">${relativeDate(item.createdAt)} · 收藏的发现</div><h3 class="saved-question">${esc(item.question)}</h3>${answerMarkup(item.data, item.id, true)}</article>`).join("") : '<div class="empty-state"><div class="empty-mark">' + icon("bookmark") + '</div><h3>值得留住的答案，都在这里</h3><p>点击回答下方的「收藏」，慢慢建立你的知识小岛。</p><button class="action-btn" data-view="chat">开始一次探索 ' + icon("arrow") + '</button></div>';
    updateSaveButtons();
  }
  async function copyAnswer(id) {
    const message = findMessage(id); if (!message?.data) return;
    const value = exportAnswer(message);
    try { await navigator.clipboard.writeText(value); toast("回答与引用已复制"); }
    catch (_) {
      const area = document.createElement("textarea"); area.value = value; area.style.position = "fixed"; area.style.left = "-9999px"; document.body.append(area); area.select();
      try { const ok = document.execCommand("copy"); toast(ok ? "回答与引用已复制" : "复制未完成，请使用导出功能。"); } catch (_) { toast("复制未完成，请使用导出功能。"); }
      area.remove();
    }
  }
  function exportAnswer(message) {
    const data = message.data || {};
    let value = String(data.answer || "");
    const citations = Array.isArray(data.citations) ? data.citations : [];
    const needsSources = citations.some((citation) => !citation.source_uri || !value.includes(citation.source_uri) || (citation.figure && !value.includes(citation.figure)));
    if (citations.length && needsSources) value += "\n\n## 原文引用\n\n" + citations.map((citation) => `- [${citation.ref}] 《${citation.doc_name}》 · Page ${citation.page}${citation.figure ? " · " + citation.figure : ""} · ${citation.source_uri || ""}`).join("\n");
    return value + `\n\n— ${Number(data.rounds_used) || 1} 轮检索 · ${citations.length} 条溯源 · ${modelModeLabel(data.model_mode)}\n`;
  }
  function downloadMarkdown(content, title) {
    const blob = new Blob([content], { type: "text/markdown;charset=utf-8" }); const url = URL.createObjectURL(blob);
    const link = document.createElement("a"); link.href = url; link.download = (title || "FGDocQA问答").replace(/[\\/:*?"<>|]/g, "-").slice(0, 70) + ".md"; document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000); toast("Markdown 文件已导出");
  }
  function exportChat() {
    const session = currentSession(); if (!session?.messages.length) { toast("先开始一次问答，再导出你的发现。"); return; }
    let value = `# ${session.title}\n\n> FGDocQA · 文档问答\n> 导出时间：${new Date().toLocaleString("zh-CN")}\n\n`;
    session.messages.forEach((message) => { if (message.role === "user") value += `## 问题\n\n${message.content}\n\n`; else if (message.role === "assistant") value += `### 回答\n\n${exportAnswer(message)}\n---\n\n`; });
    downloadMarkdown(value, session.title);
  }

  function documentCard(doc, compact = false) {
    return `<button class="${compact ? "doc-item" : "library-card"}" data-doc="${esc(doc.doc_id)}" data-page="1"><span class="doc-icon">${icon("file")}</span><span class="${compact ? "doc-meta" : "library-card-head"}"><b>${esc(doc.doc_name)}</b><span>${esc(doc.category || "未分类")} · ${Number(doc.pages) || 1} 页${compact ? "" : ` · ${Number(doc.chunks) || 0} 个片段`}</span></span>${icon("arrow")}</button>`;
  }
  function renderDocs() {
    const query = $("docFilter")?.value.trim().toLowerCase() || "";
    const docs = state.docs.filter((doc) => `${doc.doc_name} ${doc.category}`.toLowerCase().includes(query));
    text("docCount", `${docs.length} 份文档`);
    if ($("docList")) $("docList").innerHTML = docs.slice(0, query ? 30 : 3).map((doc) => documentCard(doc, true)).join("") || '<p class="history-empty">没有找到匹配的文档。</p>';
  }
  function renderLibrary() {
    if (!$("libraryResults")) return;
    const query = $("librarySearch")?.value.trim() || "";
    const category = $("libraryCategory")?.value || "";
    if (query) { searchLibrary(); return; }
    state.libraryRequest++;
    const docs = state.docs.filter((doc) => !category || doc.category === category);
    text("libraryCount", `${docs.length} 份文档`);
    $("libraryResults").innerHTML = docs.map((doc) => documentCard(doc)).join("") || '<div class="empty-state"><h3>这里暂时没有文档</h3><p>试试其他分类，或者先将文档加入知识库。</p></div>';
  }
  async function searchLibrary() {
    if (!$("libraryResults")) return;
    const query = $("librarySearch")?.value.trim() || "";
    if (!query) { renderLibrary(); return; }
    const requestId = ++state.libraryRequest;
    const category = $("libraryCategory")?.value || "";
    text("libraryCount", "正在寻找相关片段…");
    $("libraryResults").innerHTML = '<div class="empty-state"><div class="thinking"><span class="dots"><i></i><i></i><i></i></span>检索文库中…</div></div>';
    try {
      const data = await api(`/api/search?q=${encodeURIComponent(query)}&top_k=12${category ? `&category=${encodeURIComponent(category)}` : ""}`);
      if (requestId !== state.libraryRequest) return;
      const hits = Array.isArray(data.hits) ? data.hits : [];
      text("libraryCount", `${hits.length} 个相关片段 · ${Math.round(Number(data.latency_ms) || 0)} ms`);
      $("libraryResults").innerHTML = hits.map((hit) => {
        const chunk = hit.chunk || {};
        const snippet = String(chunk.text || "").replace(/<!--[^]*?-->/g, "").replace(/[#*>`]/g, "").slice(0, 280);
        return `<button class="library-card search-result" data-doc="${esc(chunk.doc_id)}" data-page="${Number(chunk.page) || 1}"><span class="library-card-head"><b>${esc(chunk.doc_name)}</b><span>${esc(chunk.category)} · 第 ${Number(chunk.page) || 1} 页</span></span><span class="library-tag">${esc(chunk.heading || "正文片段")}</span><span class="library-snippet">${esc(snippet)}</span><span class="library-card-foot">查看原文 ${icon("arrow")}</span></button>`;
      }).join("") || '<div class="empty-state"><h3>这次没有找到相关片段</h3><p>换一个关键词，或者扩大检索分类再试试。</p></div>';
    } catch (error) {
      if (requestId !== state.libraryRequest) return;
      text("libraryCount", "检索暂时不可用"); $("libraryResults").innerHTML = `<div class="error-box">${esc(error.message)}<button class="action-btn" data-library-retry>重新尝试</button></div>`;
    }
  }
  function setConnection(connected) {
    const container = document.querySelector(".library-status .status-title");
    let label = $("connectionText");
    if (!label && container) {
      label = document.createElement("span"); label.id = "connectionText";
      const oldText = [...container.childNodes].find((node) => node.nodeType === Node.TEXT_NODE && node.textContent.trim());
      if (oldText) oldText.replaceWith(label); else container.append(label);
    }
    if (label) label.textContent = connected ? "知识库已连接" : "知识库未连接";
    const dot = $("statusDot") || document.querySelector(".library-status .status-dot");
    if (dot) { dot.classList.toggle("offline", !connected); dot.style.background = connected ? "" : "#99a4b2"; dot.style.boxShadow = connected ? "" : "none"; }
  }
  async function loadStats() {
    try {
      const data = await api("/api/stats"); state.stats = data; state.docs = Array.isArray(data.docs) ? data.docs.filter((doc) => doc && typeof doc === "object") : []; setConnection(true);
      text("statDocs", state.docs.length); text("statChunks", data.stats?.chunk_count || 0); text("statImages", data.stats?.image_count || 0); text("versionText", `索引 ${data.version}`);
      const mode = ["http", "mixed"].includes(data.model?.mode) ? data.model.mode : "mock";
      const capabilities = data.model?.capabilities || {};
      const retrievalHttp = capabilities.embed?.mode === "http" || capabilities.rerank?.mode === "http";
      const extractive = capabilities.chat?.mode === "mock";
      const needsRebuild = capabilities.embed?.mode === "http" && (data.stats?.mode === "mock" || data.stats?.embedding_model?.startsWith("mock-"));
      let modeDescription = mode === "mock" ? "Mock 演示模式" : "API 已配置";
      if (retrievalHttp && extractive) modeDescription = "检索 API · 抽取回答";
      if (needsRebuild) modeDescription = "API 已配置 · 索引待重建";
      if (Object.values(capabilities).some((capability) => capability?.state === "degraded")) modeDescription = "部分接口已降级";
      $("modeBadge")?.classList.toggle("http", mode !== "mock"); text("modeText", modeDescription);
      if ($("modeBadge")) $("modeBadge").title = mode === "mock" ? "当前使用本地演示模型" : "此处展示配置和索引状态，连通性请以接口探测或实际问答结果为准";
      const options = '<option value="">全部文档</option>' + (Array.isArray(data.categories) ? data.categories : []).map((category) => `<option value="${esc(category)}">${esc(category)}</option>`).join("");
      ["categorySelect", "libraryCategory"].forEach((id) => { if ($(id)) $(id).innerHTML = options; });
      if ($("categorySelect") && currentSession()) $("categorySelect").value = currentSession().category || "";
      renderDocs(); if (state.view === "library") renderLibrary();
    } catch (error) {
      setConnection(false); text("modeText", "文库尚未就绪"); text("versionText", "请检查本地服务");
      if ($("docList")) $("docList").innerHTML = `<div class="error-box">${esc(error.message)}<button class="action-btn" data-refresh-stats>重试连接</button></div>`;
    }
  }

  function closeDrawer() {
    state.docRequest++; $("drawer")?.classList.remove("open"); $("overlay")?.classList.remove("open"); $("drawer")?.setAttribute("aria-hidden", "true");
    state.drawerReturn?.focus?.(); state.drawerReturn = null;
  }
  async function openDoc(docId, page = 1) {
    if (!docId || !$("drawer")) return;
    const requestId = ++state.docRequest;
    state.drawerReturn = document.activeElement;
    $("drawer").classList.add("open"); $("overlay")?.classList.add("open"); $("drawer").setAttribute("aria-hidden", "false");
    text("drawerTitle", "正在打开文档…"); text("drawerMeta", ""); text("drawerSource", "");
    $("drawerBody").innerHTML = '<div class="empty-state"><div class="thinking"><span class="dots"><i></i><i></i><i></i></span>正在载入原文…</div></div>';
    $("drawerClose")?.focus();
    try {
      const data = await api(`/api/doc?doc_id=${encodeURIComponent(docId)}&mode=parsed`);
      if (requestId !== state.docRequest) return;
      text("drawerTitle", data.doc_name || "文档原文"); text("drawerMeta", `${data.category || "未分类"} · ${data.page_mode === "estimated" ? "估算页码" : "原文页码"}`); text("drawerSource", data.source || "");
      const body = String(data.text || "").replace(/^---\r?\n[^]*?\r?\n---\r?\n/, "");
      const parts = body.split(/<!--\s*page:\s*(\d+)\s*-->/g);
      const sections = [];
      if (parts[0].trim()) sections.push({ page: 1, body: parts[0] });
      for (let index = 1; index < parts.length; index += 2) sections.push({ page: Number(parts[index]) || 1, body: parts[index + 1] || "" });
      if (!sections.length) sections.push({ page: 1, body: "文档暂时没有可预览的文本。" });
      const images = Array.isArray(data.images) ? data.images : [];
      const pages = [...new Set(sections.map((section) => section.page))];
      $("drawerBody").innerHTML = '<div class="reader-toolbar"><input class="reader-search" id="readerSearch" type="search" placeholder="在此文档内查找…" aria-label="在文档内查找"><span id="readerMatchCount"></span></div><div class="reader-pages">' + pages.map((number) => `<button class="reader-page ${number === Number(page) ? "active" : ""}" data-reader-page="${number}">P${number}</button>`).join("") + '</div><div class="reader-content answer">' + sections.map((section, index) => `<section class="reader-section" id="reader-pg-${section.page}-${index}" data-reader-section="${section.page}"><div class="page-sep">第 ${section.page} 页</div>${markdown(section.body, { images })}</section>`).join("") + '</div>';
      $("drawerBody").scrollTop = 0;
      requestAnimationFrame(() => jumpToPage(page));
    } catch (error) {
      if (requestId !== state.docRequest) return;
      text("drawerTitle", "暂时无法打开"); $("drawerBody").innerHTML = `<div class="error-box">${esc(error.message)}</div>`;
    }
  }
  function jumpToPage(page) {
    const target = $("drawerBody")?.querySelector(`[data-reader-section="${Number(page) || 1}"]`);
    if (target) target.scrollIntoView({ block: "start", behavior: "smooth" });
    $("drawerBody")?.querySelectorAll("[data-reader-page]").forEach((button) => button.classList.toggle("active", Number(button.dataset.readerPage) === Number(page)));
  }
  function searchReader(value) {
    const container = $("drawerBody")?.querySelector(".reader-content"); if (!container) return;
    container.querySelectorAll("mark").forEach((mark) => mark.replaceWith(document.createTextNode(mark.textContent)));
    container.normalize(); const query = value.trim();
    if (!query) { text("readerMatchCount", ""); return; }
    const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, { acceptNode(node) { return node.parentElement.closest("script,style,svg") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT; } });
    const nodes = []; while (walker.nextNode()) nodes.push(walker.currentNode);
    let count = 0;
    for (const node of nodes) {
      const raw = node.nodeValue; const lower = raw.toLocaleLowerCase(); const needle = query.toLocaleLowerCase();
      if (!lower.includes(needle)) continue;
      const fragment = document.createDocumentFragment(); let cursor = 0, match;
      while ((match = lower.indexOf(needle, cursor)) !== -1 && count < 500) {
        fragment.append(document.createTextNode(raw.slice(cursor, match))); const mark = document.createElement("mark"); mark.textContent = raw.slice(match, match + query.length); fragment.append(mark); cursor = match + query.length; count++;
      }
      fragment.append(document.createTextNode(raw.slice(cursor))); node.replaceWith(fragment);
    }
    text("readerMatchCount", `${count}${count >= 500 ? "+" : ""} 处`); container.querySelector("mark")?.scrollIntoView({ block: "center", behavior: "smooth" });
  }
  function resizeInput() {
    if (!$("input")) return; $("input").style.height = "auto"; $("input").style.height = Math.min(140, $("input").scrollHeight) + "px";
  }
  // One delegated handler also supports newly rendered saved answers and cards.
  document.addEventListener("click", (event) => {
    const target = event.target.closest("button,a"); if (!target) return;
    if (target.dataset.view) { setView(target.dataset.view); return; }
    if (target.dataset.q) { ask(target.dataset.q); return; }
    if (target.dataset.doc) { openDoc(target.dataset.doc, Number(target.dataset.page) || 1); return; }
    if (target.dataset.copy) { copyAnswer(target.dataset.copy); return; }
    if (target.dataset.save) { toggleSave(target.dataset.save); return; }
    if (target.dataset.exportAnswer) { const message = findMessage(target.dataset.exportAnswer); if (message) downloadMarkdown(`# ${message.question || "文档问答"}\n\n${exportAnswer(message)}`, message.question); return; }
    if (target.dataset.retry) { const message = findMessage(target.dataset.retry); if (message) ask(message.question, message); return; }
    if (target.dataset.session) { state.activeId = target.dataset.session; persist(); renderHistory(); renderChat(); setView("chat"); return; }
    if (target.dataset.deleteSession) {
      const id = target.dataset.deleteSession;
      state.sessions = state.sessions.filter((session) => session.id !== id);
      if (state.activeId === id) state.activeId = null;
      persist(); renderHistory(); renderChat(); if (state.view !== "chat") setView(state.view); toast("会话已删除，收藏的回答会保留。"); return;
    }
    if (target.dataset.readerPage) { jumpToPage(target.dataset.readerPage); return; }
    if (target.hasAttribute("data-library-retry")) { searchLibrary(); return; }
    if (target.hasAttribute("data-refresh-stats")) loadStats();
  });
  on("newChat", "click", newChat);
  on("send", "click", () => ask($("input")?.value));
  on("exportChat", "click", exportChat);
  on("input", "input", resizeInput);
  on("input", "keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing && event.keyCode !== 229) { event.preventDefault(); ask(event.target.value); }
  });
  on("docFilter", "input", renderDocs);
  on("categorySelect", "change", () => { const session = currentSession(); if (session) { session.category = $("categorySelect").value; persist(); } });
  on("libraryCategory", "change", renderLibrary);
  on("librarySearchBtn", "click", searchLibrary);
  on("librarySearch", "keydown", (event) => { if (event.key === "Enter" && !event.isComposing) { clearTimeout(searchLibrary.timer); searchLibrary(); } });
  on("librarySearch", "input", () => { clearTimeout(searchLibrary.timer); state.libraryRequest++; searchLibrary.timer = setTimeout(() => renderLibrary(), 350); });
  on("drawerClose", "click", closeDrawer);
  on("overlay", "click", closeDrawer);
  on("drawerBody", "input", (event) => { if (event.target.id === "readerSearch") { clearTimeout(searchReader.timer); searchReader.timer = setTimeout(() => searchReader(event.target.value), 180); } });
  on("mobileMenu", "click", () => { const open = $("sidebar")?.classList.toggle("open"); $("sidebarOverlay")?.classList.toggle("open", open); $("mobileMenu")?.setAttribute("aria-expanded", String(!!open)); });
  on("sidebarOverlay", "click", closeSidebar);
  document.addEventListener("keydown", (event) => {
    if (!event.isComposing && event.keyCode !== 229 && (event.metaKey || event.ctrlKey) && !event.altKey && event.key.toLowerCase() === "k") { event.preventDefault(); newChat(); return; }
    if (event.key === "Escape") { closeDrawer(); closeSidebar(); }
    if (event.key === "/" && !event.metaKey && !event.ctrlKey && !event.altKey && !event.target.closest("input,textarea,select,[contenteditable=true]")) { event.preventDefault(); setView("chat"); $("input")?.focus(); }
    if (event.key === "Tab" && $("drawer")?.classList.contains("open")) {
      const focusable = [...$("drawer").querySelectorAll('button,a[href],input,select,textarea,[tabindex="0"]')].filter((element) => !element.disabled && element.offsetParent !== null);
      const first = focusable[0], last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }
  });
  window.addEventListener("resize", resizeInput);
  restore(); renderHistory(); renderChat(); updateSaveButtons(); setView("chat"); resizeInput();
  loadStats();
  const initialQuestion = new URLSearchParams(location.search).get("q");
  if (initialQuestion) { if ($("input")) $("input").value = initialQuestion.slice(0, 2000); resizeInput(); }
})();
