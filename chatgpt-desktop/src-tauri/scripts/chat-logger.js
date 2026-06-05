/**
 * @name chat-logger.js
 * @version 0.8.6
 * @url https://github.com/thanhiont423/mem0custom
 *
 * v0.6.0 — NHE TOI DA (fix lag) + nut noi:
 *   - BO MutationObserver characterData (nguon lag: fire moi token khi stream).
 *   - Quan sat CHI childList + subtree + debounce 500ms -> quet gon khi co tin moi/xong.
 *   - Giu hook Enter bat keyword tai DOM (theo yeu cau).
 *   - Them hop nut noi: [Luu summary] (summarize_current) + [Luu full session] (compact).
 *
 * v0.5.0: doc keywords tu window.__INJECTED_KEYWORDS__ (CSP-safe), emit qua event.
 */

const DEFAULT_KEYWORDS = {
  "compact": "compact_session",
  "lưu": "compact_session",
  "luu": "compact_session",
  "/compact": "compact_session",
  "/lưu": "compact_session",
};

// Lệnh prefix (gõ kèm điều kiện phía sau): "/lichsu deploy VPS" -> tìm theo "deploy VPS".
const PREFIX_COMMANDS = ["lichsu", "lịch sử", "lich su", "history"];
const DETAIL_COMMANDS = ["xemphien", "xem phiên", "xem phien", "chitiet"];

class ChatLogger {
  static loggedIds = new Set();
  static observer = null;
  static emitMethod = null;
  static scanTimer = null;
  static keywords = (typeof window !== "undefined" && window.__INJECTED_KEYWORDS__)
    ? window.__INJECTED_KEYWORDS__
    : DEFAULT_KEYWORDS;

  static start() {
    const tryAttach = () => {
      const target = document.querySelector("main");
      if (!target) {
        setTimeout(tryAttach, 1000);
        return;
      }
      if (ChatLogger.observer) ChatLogger.observer.disconnect();
      // NHE: chi childList + subtree, KHONG characterData (khong fire moi token).
      // Gop cac thay doi bang debounce 500ms -> quet toi da ~2 lan/giay khi co node moi.
      ChatLogger.observer = new MutationObserver(() => ChatLogger.scheduleScan());
      ChatLogger.observer.observe(target, { childList: true, subtree: true });
      ChatLogger.scan();              // quet 1 lan luc gan
      ChatLogger.hookKeywordTrigger();
      ChatLogger.detectEmitMethod();
      ChatLogger.mountFloatingButtons();
      ChatLogger.listenResult();
      ChatLogger.checkOAuth();
      ChatLogger.checkConfigStatus();
      console.log("[chat-logger v0.8.0] attached (+ kiểm tra/gia hạn OAuth token)");
    };
    tryAttach();
  }

  // Debounce: gom nhieu mutation thanh 1 lan quet -> tranh quet lien tuc luc stream.
  static scheduleScan() {
    if (ChatLogger.scanTimer) clearTimeout(ChatLogger.scanTimer);
    ChatLogger.scanTimer = setTimeout(() => {
      ChatLogger.scan();
      ChatLogger.hookKeywordTrigger();
    }, 500);
  }

  static detectEmitMethod() {
    if (window.__TAURI__?.event?.emit) {
      ChatLogger.emitMethod = "event";
    } else if (window.__TAURI_INTERNALS__?.postMessage) {
      ChatLogger.emitMethod = "internals";
    } else if (window.__TAURI__?.core?.invoke) {
      ChatLogger.emitMethod = "invoke";
    } else {
      console.error("[chat-logger] NO Tauri bridge found");
    }
  }

  static getConvId() {
    const m = location.pathname.match(/\/c\/([^/?#]+)/);
    return m ? m[1] : "default";
  }

  static matchKeyword(text) {
    const t = (text || "").trim().toLowerCase();
    for (const [kw, action] of Object.entries(ChatLogger.keywords)) {
      if (t === kw.toLowerCase()) return action;
    }
    return null;
  }

  static scan() {
    const nodes = document.querySelectorAll("[data-message-id]");
    const convId = ChatLogger.getConvId();
    nodes.forEach((node) => {
      const id = node.dataset.messageId;
      const role = node.dataset.messageAuthorRole;
      if (!id || !role) return;
      if (ChatLogger.loggedIds.has(id)) return;
      const content = (node.innerText || "").trim();
      if (!content) return;
      if (ChatLogger.matchKeyword(content)) {
        ChatLogger.loggedIds.add(id);
        return;
      }
      if (role === "user") {
        ChatLogger.send(id, convId, role, content);
      } else if (role === "assistant") {
        const turnContainer =
          node.closest('[data-testid^="conversation-turn-"]') ||
          node.parentElement;
        const done =
          turnContainer &&
          (turnContainer.querySelector('[data-testid*="copy"]') ||
            turnContainer.querySelector('button[aria-label*="Copy" i]'));
        if (done) ChatLogger.send(id, convId, role, content);
      }
    });
  }

  static hookKeywordTrigger() {
    const textareas = document.querySelectorAll('textarea, [contenteditable="true"]');
    textareas.forEach((ta) => {
      if (ta.dataset.kwHooked === "1") return;
      ta.dataset.kwHooked = "1";
      const handler = (e) => {
        const text = (ta.value !== undefined ? ta.value : ta.innerText || "").trim();
        // Lệnh lịch sử dạng prefix: "/lichsu [điều kiện]" -> tìm theo điều kiện (rỗng = 5 gần nhất)
        const low = text.toLowerCase();
        const pref = PREFIX_COMMANDS.find((c) => low === c || low.startsWith(c + " "));
        if (pref) {
          e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation();
          const query = text.slice(pref.length).trim();
          console.log(`[chat-logger] history query='${query}'`);
          ChatLogger.triggerFetchHistory(query);
          ChatLogger.clearInput(ta);
          return;
        }
        const det = DETAIL_COMMANDS.find((c) => low.startsWith(c + " "));
        if (det) {
          e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation();
          const id = text.slice(det.length).trim();
          ChatLogger.triggerFetchDetail(id);
          ChatLogger.clearInput(ta);
          return;
        }
        const action = ChatLogger.matchKeyword(text);
        if (!action) return;
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation();
        console.log(`[chat-logger] keyword '${text}' -> '${action}'`);
        ChatLogger.triggerAction(action);
        ChatLogger.clearInput(ta);
      };
      ta.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) handler(e);
      }, true);
    });
  }

  // ===== Nut noi: Luu summary + Luu full session =====
  static mountFloatingButtons() {
    if (document.getElementById("cl-fab")) return;
    if (!document.body) { setTimeout(ChatLogger.mountFloatingButtons, 800); return; }
    const box = document.createElement("div");
    box.id = "cl-fab";
    box.style.cssText =
      "position:fixed;right:18px;bottom:96px;z-index:2147483647;display:flex;" +
      "flex-direction:column;gap:8px;font-family:system-ui,sans-serif;";

    const mkBtn = (label, title, onClick) => {
      const b = document.createElement("button");
      b.textContent = label;
      b.title = title;
      b.style.cssText =
        "padding:8px 12px;border:none;border-radius:18px;cursor:pointer;" +
        "background:#10a37f;color:#fff;font-size:12px;font-weight:600;" +
        "box-shadow:0 2px 8px rgba(0,0,0,.25);white-space:nowrap;opacity:.85;";
      b.onmouseenter = () => (b.style.opacity = "1");
      b.onmouseleave = () => (b.style.opacity = ".85");
      b.dataset.label = label;
      b.onclick = (e) => {
        e.preventDefault();
        b.textContent = "⏳ Đang lưu...";
        b.disabled = true;
        onClick();
        // fallback: nếu 12s không có phản hồi -> coi như timeout
        clearTimeout(b._t);
        b._t = setTimeout(() => ChatLogger.setBtnState(b, false, "Không có phản hồi (timeout)"), 12000);
      };
      return b;
    };

    const bSum = mkBtn("📝 Lưu summary", "Tom tat phien va luu vao mem0",
      () => ChatLogger.triggerAction("summarize_current"));
    const bFull = mkBtn("💾 Lưu full session", "Luu toan bo phien (full transcript)",
      () => ChatLogger.triggerAction("compact_session"));
    const bRefresh = mkBtn("🔄 Gia hạn token", "Token Claude hết hạn — bấm để tự gia hạn",
      () => ChatLogger.triggerRefreshOAuth());
    bRefresh.style.background = "#d9534f";
    bRefresh.style.display = "none";

    // v0.8.5: nút Lịch sử — toggle panel của riêng app (không inject ChatGPT)
    const bHist = document.createElement("button");
    bHist.textContent = "🕐 Lịch sử";
    bHist.title = "Mở panel lịch sử phiên (browse mà không tốn token)";
    bHist.style.cssText =
      "padding:8px 12px;border:none;border-radius:18px;cursor:pointer;" +
      "background:#6b46c1;color:#fff;font-size:12px;font-weight:600;" +
      "box-shadow:0 2px 8px rgba(0,0,0,.25);white-space:nowrap;opacity:.85;";
    bHist.onmouseenter = () => (bHist.style.opacity = "1");
    bHist.onmouseleave = () => (bHist.style.opacity = ".85");
    bHist.onclick = (e) => { e.preventDefault(); ChatLogger.toggleHistoryPanel(); };

    ChatLogger.btns = { summarize: bSum, compact: bFull, refresh: bRefresh, history: bHist };
    box.appendChild(bSum);
    box.appendChild(bFull);
    box.appendChild(bRefresh);
    box.appendChild(bHist);
    document.body.appendChild(box);
  }

  static listenResult() {
    if (ChatLogger._resultBound) return;
    ChatLogger._resultBound = true;
    try {
      if (window.__TAURI__?.event?.listen) {
        window.__TAURI__.event.listen("chat-logger://history-result",
          (e) => ChatLogger.renderHistory(e.payload));
        window.__TAURI__.event.listen("chat-logger://oauth-status",
          (e) => ChatLogger.onOAuthStatus(e.payload));
        window.__TAURI__.event.listen("chat-logger://session-detail-result",
          (e) => ChatLogger.renderDetail(e.payload));
      }
    } catch (err) { console.error("[chat-logger] listen extra failed:", err); }
    const handle = (payload) => {
      const p = payload || {};
      const btn = p.action === "summarize" ? (ChatLogger.btns && ChatLogger.btns.summarize)
                : p.action === "compact"   ? (ChatLogger.btns && ChatLogger.btns.compact)
                : null;
      if (btn) ChatLogger.setBtnState(btn, !!p.ok, p.msg || "");
      ChatLogger.toast(p.ok, p.msg || (p.ok ? "Thành công" : "Thất bại"));
    };
    try {
      if (window.__TAURI__?.event?.listen) {
        window.__TAURI__.event.listen("chat-logger://result", (e) => handle(e.payload));
      }
    } catch (err) { console.error("[chat-logger] listenResult failed:", err); }
  }

  static setBtnState(btn, ok, msg) {
    if (!btn) return;
    clearTimeout(btn._t);
    btn.disabled = false;
    btn.title = msg || btn.title;
    btn.textContent = ok ? "✓ " + (btn.dataset.label || "Đã lưu") : "✗ Lỗi";
    btn.style.background = ok ? "#10a37f" : "#d9534f";
    setTimeout(() => {
      btn.textContent = btn.dataset.label || btn.textContent;
      btn.style.background = "#10a37f";
    }, 2500);
  }

  static toast(ok, msg) {
    if (!document.body) return;
    const t = document.createElement("div");
    t.textContent = (ok ? "✓ " : "✗ ") + msg;
    t.style.cssText =
      "position:fixed;right:18px;bottom:150px;z-index:2147483647;max-width:280px;" +
      "padding:10px 14px;border-radius:8px;color:#fff;font-size:13px;font-family:system-ui,sans-serif;" +
      "box-shadow:0 4px 12px rgba(0,0,0,.3);opacity:0;transition:opacity .2s;" +
      "background:" + (ok ? "#10a37f" : "#d9534f") + ";";
    document.body.appendChild(t);
    const raf = (typeof requestAnimationFrame !== "undefined") ? requestAnimationFrame : (cb)=>setTimeout(cb,16);
    raf(() => (t.style.opacity = "1"));
    setTimeout(() => { t.style.opacity = "0"; setTimeout(() => t.remove(), 300); }, 3500);
  }

  static triggerAction(action) {
    try {
      if (action === "fetch_history") {
        ChatLogger.triggerFetchHistory("");
        return;
      }
      if (action === "compact_session") {
        ChatLogger.triggerCompact();
      } else if (ChatLogger.emitMethod === "event") {
        window.__TAURI__.event.emit(`chat-logger://${action}`, {});
      } else if (ChatLogger.emitMethod === "internals") {
        window.__TAURI_INTERNALS__.postMessage({ cmd: action });
      } else if (ChatLogger.emitMethod === "invoke") {
        window.__TAURI__.core.invoke(action).catch((e) =>
          console.error(`[chat-logger] invoke '${action}' failed:`, e));
      }
    } catch (err) {
      console.error(`[chat-logger] triggerAction '${action}' failed:`, err);
    }
  }

  static renderHistory(payload) {
    const p = payload || {};
    let sessions = p.sessions;
    if (sessions && !Array.isArray(sessions) && Array.isArray(sessions.sessions)) sessions = sessions.sessions;
    if (!Array.isArray(sessions)) sessions = [];

    // PANEL MODE: render vào panel app riêng (v0.8.5+)
    if (ChatLogger._panelMode) {
      ChatLogger._panelMode = false;
      if (!p.ok) {
        const body = document.getElementById("cl-hist-body");
        if (body) {
          const cfg = ChatLogger._cfgStatus || {};
          const isCfgErr = cfg.sync && cfg.sync !== "ready";
          const detail = isCfgErr ? cfg.sync_detail : (p.msg || "?");
          body.innerHTML =
            '<div style="padding:14px;background:#fdecea;border-left:3px solid #d9534f;margin:0">' +
              '<div style="font-size:12px;color:#a72020;margin-bottom:6px;font-weight:600">⚠ Không lấy được lịch sử</div>' +
              '<div style="font-size:11px;color:#5f1e1e;margin-bottom:8px;line-height:1.5">' + ChatLogger._esc(detail) + '</div>' +
              (isCfgErr ? '<button id="cl-hist-open-cfg" style="font-size:11px;padding:5px 10px;background:#fff;border:1px solid #d9534f;border-radius:4px;color:#a72020;cursor:pointer">📁 Mở thư mục config</button>' : '') +
              (isCfgErr && cfg.data_dir ? '<div style="font-size:10px;color:#888;margin-top:6px;word-break:break-all">' + ChatLogger._esc(cfg.data_dir) + '</div>' : '') +
            '</div>';
          const btn = body.querySelector("#cl-hist-open-cfg");
          if (btn) btn.onclick = () => ChatLogger.openConfigDir();
        }
        return;
      }
      ChatLogger.renderHistoryInPanel(sessions);
      return;
    }

    // LEGACY KEYWORD MODE
    if (!p.ok) {
      ChatLogger.insertIntoChat("⚠️ Không lấy được lịch sử: " + (p.msg || "lỗi không rõ"));
      return;
    }
    if (sessions.length === 0) {
      ChatLogger.insertIntoChat("📜 Lịch sử trống — chưa có phiên nào được lưu.");
      return;
    }
    const lines = ["📜 **" + sessions.length + " phiên gần nhất:**", ""];
    sessions.forEach((snap, i) => {
      const id = snap.id || "?";
      const when = snap.started_at || snap.created_at || "";
      const sum = (snap.summary || snap.llm_summary || "(chưa có tóm tắt)").toString().slice(0, 160);
      const cnt = snap.message_count != null ? ` · ${snap.message_count} tin` : "";
      lines.push(`${i + 1}. [${when}]${cnt}\n   ${sum}\n   id: ${id}`);
    });
    ChatLogger.insertIntoChat(lines.join("\n"));
  }

  // Chèn text vào ô nhập ChatGPT (để hiện trong khung chat / làm ngữ cảnh).
  // v0.8.4+: ChatGPT mới dùng ProseMirror <div contenteditable> chứ không phải <textarea>.
  // innerText + dispatchEvent("input") bị React overwrite -> dùng paste event + execCommand fallback.
  static findChatGPTInput() {
    return document.querySelector('#prompt-textarea')
        || document.querySelector('div.ProseMirror[contenteditable="true"]')
        || [...document.querySelectorAll('div[contenteditable="true"]')].find(el => el.offsetParent !== null)
        || document.querySelector('main textarea')
        || document.querySelector('textarea');
  }

  static insertIntoChat(text) {
    const ta = ChatLogger.findChatGPTInput();
    if (!ta) {
      console.error("[chat-logger] insertIntoChat: KHÔNG tìm thấy input ChatGPT");
      ChatLogger.toast(false, "Không tìm thấy ô chat — xem console");
      return;
    }
    ta.focus();
    try {
      if (ta.tagName === "TEXTAREA" || ta.value !== undefined) {
        const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value").set;
        setter.call(ta, text);
        ta.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: text }));
      } else {
        const dt = new DataTransfer();
        dt.setData("text/plain", text);
        const pasteEvent = new ClipboardEvent("paste", { bubbles: true, cancelable: true, clipboardData: dt });
        const accepted = ta.dispatchEvent(pasteEvent);
        if (!accepted || ta.innerText.trim() === "") {
          try { document.execCommand("insertText", false, text); } catch (e) {}
        }
      }
    } catch (err) {
      console.error("[chat-logger] insertIntoChat fail:", err);
      ChatLogger.toast(false, "Lỗi chèn: " + (err.message || err));
      return;
    }
    setTimeout(() => {
      const got = (ta.value !== undefined ? ta.value : ta.innerText) || "";
      if (got.trim() === "") {
        console.error("[chat-logger] insertIntoChat: chèn xong nhưng input vẫn rỗng. Element:", ta);
        ChatLogger.toast(false, "Chèn thất bại — text đã copy vào clipboard, Ctrl+V");
        try { navigator.clipboard.writeText(text); } catch (e) {}
      } else {
        ChatLogger.toast(true, "Đã chèn lịch sử vào ô chat — Enter để gửi/đọc");
      }
    }, 200);
  }


  static triggerFetchDetail(id) {
    if (!id) { ChatLogger.toast(false, "Thiếu id phiên (vd: xemphien <id>)"); return; }
    ChatLogger.toast(true, "Đang lấy chi tiết phiên...");
    try {
      if (ChatLogger.emitMethod === "event") {
        window.__TAURI__.event.emit("chat-logger://fetch-session-detail", { id });
      } else if (ChatLogger.emitMethod === "internals") {
        window.__TAURI_INTERNALS__.postMessage({ cmd: "fetch_session_detail", id });
      }
    } catch (err) { console.error("[chat-logger] fetchDetail failed:", err); }
  }

  static renderDetail(payload) {
    const p = payload || {};
    const sn = p.session || {};

    // Inject-full mode: chèn full transcript vào ô ChatGPT
    if (ChatLogger._pendingInjectFull) {
      ChatLogger._pendingInjectFull = null;
      if (!p.ok) { ChatLogger.toast(false, "Không lấy được chi tiết để inject: " + (p.msg || "?")); return; }
      let tr = sn.transcript;
      if (typeof tr === "string") { try { tr = JSON.parse(tr); } catch (e) {} }
      if (!Array.isArray(tr)) tr = [];
      const head = "Context từ phiên cũ (" + (sn.started_at || "").slice(0, 16) + ", " + tr.length + " tin):\n\n";
      const body = tr.map((m) => "[" + (m.role || m.author || "?") + "] " + (m.content || m.text || "").toString()).join("\n\n");
      ChatLogger.insertIntoChat(head + body);
      return;
    }

    // Modal mode (v0.8.5+)
    if (ChatLogger._modalMode) {
      ChatLogger._modalMode = false;
      if (!p.ok) {
        const body = document.getElementById("cl-modal-body");
        if (body) body.innerHTML = '<div style="text-align:center;color:#d9534f;padding:20px">⚠️ ' + ChatLogger._esc(p.msg || "lỗi") + '</div>';
        return;
      }
      ChatLogger.renderDetailInModal(sn);
      return;
    }

    // LEGACY KEYWORD MODE
    if (!p.ok) { ChatLogger.insertIntoChat("⚠️ Không lấy được chi tiết: " + (p.msg || "lỗi")); return; }
    let tr = sn.transcript;
    if (typeof tr === "string") { try { tr = JSON.parse(tr); } catch (e) {} }
    if (!Array.isArray(tr)) tr = [];
    const head = `📄 Phiên ${sn.id || ""} — ${sn.started_at || ""} · ${tr.length} tin\n`;
    const body = tr.map((m) => "[" + (m.role || m.author || "?") + "] " + (m.content || m.text || "").toString().slice(0, 2000)).join("\n\n");
    ChatLogger.insertIntoChat(head + "\n" + body);
  }

  static clearInput(ta) {
    if (ta.value !== undefined) {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value").set;
      setter.call(ta, "");
      ta.dispatchEvent(new InputEvent("input", { bubbles: true }));
    } else {
      ta.innerText = "";
      ta.dispatchEvent(new InputEvent("input", { bubbles: true }));
    }
  }

  // Lấy lịch sử: query rỗng = 5 phiên gần nhất; có query = tìm theo điều kiện.
  static triggerFetchHistory(query) {
    ChatLogger.toast(true, query ? `Đang tìm lịch sử: ${query}` : "Đang lấy 5 phiên gần nhất...");
    const payload = { query: query || "" };
    try {
      if (ChatLogger.emitMethod === "event") {
        window.__TAURI__.event.emit("chat-logger://fetch-history", payload);
      } else if (ChatLogger.emitMethod === "internals") {
        window.__TAURI_INTERNALS__.postMessage({ cmd: "fetch_history", ...payload });
      }
    } catch (err) { console.error("[chat-logger] fetchHistory failed:", err); }
  }

  static checkOAuth() {
    try {
      if (window.__TAURI__?.event?.emit) {
        window.__TAURI__.event.emit("chat-logger://check-oauth", {});
      }
    } catch (err) { console.error("[chat-logger] checkOAuth failed:", err); }
  }

  static triggerRefreshOAuth() {
    const b = ChatLogger.btns && ChatLogger.btns.refresh;
    if (b) { b.textContent = "⏳ Đang gia hạn..."; b.disabled = true; }
    try {
      if (window.__TAURI__?.event?.emit) {
        window.__TAURI__.event.emit("chat-logger://refresh-oauth", {});
      }
    } catch (err) { console.error("[chat-logger] refresh failed:", err); }
  }

  // Xử lý trạng thái token: valid | expired | missing
  static onOAuthStatus(payload) {
    const p = payload || {};
    const st = p.status || "valid";
    const bSum = ChatLogger.btns && ChatLogger.btns.summarize;
    const bRef = ChatLogger.btns && ChatLogger.btns.refresh;
    if (st === "valid") {
      if (bSum) { bSum.style.background = "#10a37f"; bSum.title = "Tóm tắt phiên và lưu vào mem0"; bSum.disabled = false; }
      if (bRef) { bRef.style.display = "none"; bRef.disabled = false; bRef.textContent = "🔄 Gia hạn token"; }
      if (p.refreshed) ChatLogger.toast(true, p.msg || "Token còn hạn");
      return;
    }
    // expired hoặc missing -> báo đỏ nút summary + hiện nút gia hạn
    if (bSum) {
      bSum.style.background = "#d9534f";
      bSum.title = st === "missing"
        ? "Chưa có credentials.json — sẽ thử provider OpenAI khi tóm tắt"
        : "Token Claude hết hạn — bấm 'Gia hạn token'";
    }
    if (bRef) { bRef.style.display = "block"; bRef.disabled = false; bRef.textContent = "🔄 Gia hạn token"; }
    if (p.refreshed === false) ChatLogger.toast(false, p.msg || "Token hết hạn");
  }

  // ============== v0.8.5+ HISTORY PANEL + v0.8.6 STATUS CHECK ==============

  static checkConfigStatus() {
    if (!window.__TAURI__?.core?.invoke) return;
    window.__TAURI__.core.invoke("check_config_status").then((r) => {
      ChatLogger._cfgStatus = r;
      ChatLogger._dataDir = r.data_dir;
      const bHist = ChatLogger.btns && ChatLogger.btns.history;
      if (!bHist) return;
      if (r.sync !== "ready") {
        bHist.style.background = "#d9534f";
        bHist.title = "⚠ Chưa config archive: " + r.sync_detail;
      } else if (r.summarize !== "ready" && r.summarize !== "fallback_only") {
        bHist.style.background = "#f0ad4e";
        bHist.title = "⚠ Summarize chưa config: " + r.summarize_detail;
      } else {
        bHist.style.background = "#6b46c1";
        bHist.title = "Mở panel lịch sử phiên · " + r.sync_detail;
      }
    }).catch((e) => console.error("[chat-logger] check_config_status fail:", e));
  }

  static openConfigDir() {
    if (!window.__TAURI__?.core?.invoke) return;
    window.__TAURI__.core.invoke("open_config_dir")
      .then((p) => ChatLogger.toast(true, "Đã mở: " + p))
      .catch((e) => ChatLogger.toast(false, "Mở folder fail: " + e));
  }

  // Heuristic fix mojibake (UTF-8 decoded as Latin-1)
  static _fixMojibake(s) {
    if (typeof s !== "string" || !s) return s;
    if (!/[ÃÂäÅ]|áº|á»|Ä‘|Ä±/.test(s)) return s;
    try {
      const bytes = new Uint8Array(s.length);
      for (let i = 0; i < s.length; i++) {
        const c = s.charCodeAt(i);
        if (c > 0xff) return s;
        bytes[i] = c;
      }
      const decoded = new TextDecoder("utf-8", { fatal: false }).decode(bytes);
      if (/[ăâêôơưđáàảãạéèẻẽẹíìỉĩịóòỏõọúùủũụýỳỷỹỵ]/i.test(decoded)) return decoded;
      return s;
    } catch (e) { return s; }
  }

  static _esc(s) {
    return String(s).replace(/[&<>"]/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
  }

  static _copyId(id) {
    try { navigator.clipboard.writeText(id); ChatLogger.toast(true, "Đã copy id: " + id.slice(0, 12) + "..."); }
    catch (e) { ChatLogger.toast(false, "Copy fail: " + e.message); }
  }

  static toggleHistoryPanel() {
    const existing = document.getElementById("cl-hist-panel");
    if (existing) { existing.remove(); return; }
    ChatLogger.mountHistoryPanel("");
    ChatLogger.fetchHistoryForPanel("");
  }

  static mountHistoryPanel(initialQuery) {
    if (!document.body) return;
    if (document.getElementById("cl-hist-panel")) return;
    const panel = document.createElement("div");
    panel.id = "cl-hist-panel";
    panel.style.cssText =
      "position:fixed;top:80px;right:18px;width:30vw;min-width:380px;max-width:720px;max-height:75vh;z-index:2147483646;" +
      "background:#fff;border:1px solid rgba(0,0,0,.15);border-radius:12px;overflow:hidden;" +
      "display:flex;flex-direction:column;font-family:system-ui,sans-serif;color:#000;" +
      "box-shadow:0 4px 16px rgba(0,0,0,.2);";

    const header = document.createElement("div");
    header.style.cssText = "padding:10px 14px;border-bottom:1px solid #eee;display:flex;justify-content:space-between;align-items:center;";
    header.innerHTML = '<div style="display:flex;align-items:center;gap:8px;font-size:13px;font-weight:600"><span>🕐</span><span>Lịch sử phiên</span></div>' +
      '<span id="cl-hist-close" style="cursor:pointer;font-size:18px;color:#888;line-height:1">×</span>';
    panel.appendChild(header);
    header.querySelector("#cl-hist-close").onclick = () => panel.remove();

    const searchBox = document.createElement("div");
    searchBox.style.cssText = "padding:8px 14px;border-bottom:1px solid #eee;";
    const searchInput = document.createElement("input");
    searchInput.type = "text";
    searchInput.placeholder = "Tìm theo nội dung... (Enter)";
    searchInput.value = initialQuery || "";
    searchInput.style.cssText = "width:100%;padding:5px 8px;font-size:12px;border:1px solid #ddd;border-radius:4px;outline:none;box-sizing:border-box;";
    searchInput.onkeydown = (e) => {
      if (e.key === "Enter") {
        e.preventDefault(); e.stopPropagation();
        ChatLogger.fetchHistoryForPanel(searchInput.value.trim());
      }
    };
    searchBox.appendChild(searchInput);
    panel.appendChild(searchBox);

    const body = document.createElement("div");
    body.id = "cl-hist-body";
    body.style.cssText = "flex:1;overflow-y:auto;min-height:80px;";
    body.innerHTML = '<div style="padding:16px;text-align:center;font-size:12px;color:#888">Đang tải...</div>';
    panel.appendChild(body);

    const footer = document.createElement("div");
    footer.id = "cl-hist-footer";
    footer.style.cssText = "padding:6px 14px;border-top:1px solid #eee;font-size:11px;color:#888;background:#fafafa;text-align:center;";
    footer.textContent = "—";
    panel.appendChild(footer);
    document.body.appendChild(panel);
  }

  static fetchHistoryForPanel(query) {
    const body = document.getElementById("cl-hist-body");
    if (body) body.innerHTML = '<div style="padding:16px;text-align:center;font-size:12px;color:#888">Đang tìm...</div>';
    ChatLogger._panelMode = true;
    ChatLogger._panelQuery = query;
    try {
      if (ChatLogger.emitMethod === "event") {
        window.__TAURI__.event.emit("chat-logger://fetch-history", { query: query || "" });
      } else if (ChatLogger.emitMethod === "internals") {
        window.__TAURI_INTERNALS__.postMessage({ cmd: "fetch_history", query: query || "" });
      }
    } catch (err) {
      console.error("[chat-logger] fetchHistoryForPanel failed:", err);
      if (body) body.innerHTML = '<div style="padding:16px;text-align:center;font-size:12px;color:#d9534f">Lỗi: ' + err.message + '</div>';
    }
  }

  static renderHistoryInPanel(sessions) {
    const body = document.getElementById("cl-hist-body");
    const footer = document.getElementById("cl-hist-footer");
    if (!body) return;
    if (!Array.isArray(sessions) || sessions.length === 0) {
      body.innerHTML = '<div style="padding:16px;text-align:center;font-size:12px;color:#888">Không có phiên nào.</div>';
      if (footer) footer.textContent = "0 phiên";
      return;
    }
    body.innerHTML = "";
    sessions.forEach((sn) => {
      const id = sn.id || "?";
      const when = (sn.started_at || sn.created_at || "").toString().slice(0, 16).replace("T", " ");
      const sum = ChatLogger._fixMojibake((sn.summary || sn.llm_summary || "(chưa có tóm tắt)").toString().slice(0, 200));
      const cnt = sn.message_count != null ? sn.message_count + " tin" : "?";
      const row = document.createElement("div");
      row.style.cssText = "padding:9px 14px;border-bottom:1px solid #eee;";
      row.innerHTML =
        '<div style="font-size:11px;color:#666;margin-bottom:3px">' + when + ' · ' + cnt + '</div>' +
        '<div style="font-size:12px;color:#000;margin-bottom:6px;line-height:1.4">' + ChatLogger._esc(sum) + '</div>' +
        '<div style="display:flex;gap:5px;flex-wrap:wrap" data-id="' + ChatLogger._esc(id) + '">' +
        '<span class="cl-chip cl-chip-copy" style="font-size:10px;padding:2px 8px;border:1px solid #ddd;border-radius:10px;color:#555;cursor:pointer">📋 id</span>' +
        '<span class="cl-chip cl-chip-detail" style="font-size:10px;padding:2px 8px;border:1px solid #ddd;border-radius:10px;color:#555;cursor:pointer">👁 xem</span>' +
        '<span class="cl-chip cl-chip-inj-sum" style="font-size:10px;padding:2px 8px;background:#e3f2fd;border:1px solid #90caf9;border-radius:10px;color:#1565c0;cursor:pointer">← tóm tắt</span>' +
        '<span class="cl-chip cl-chip-inj-full" style="font-size:10px;padding:2px 8px;background:#fff3e0;border:1px solid #ffb74d;border-radius:10px;color:#e65100;cursor:pointer">⇐ full</span>' +
        '</div>';
      row.querySelector(".cl-chip-copy").onclick = () => ChatLogger._copyId(id);
      row.querySelector(".cl-chip-detail").onclick = () => ChatLogger.openDetailModal(id);
      row.querySelector(".cl-chip-inj-sum").onclick = () => ChatLogger.injectSummary(sn);
      row.querySelector(".cl-chip-inj-full").onclick = () => ChatLogger.injectFull(id);
      body.appendChild(row);
    });
    if (footer) footer.textContent = sessions.length + " phiên";
  }

  static injectSummary(sn) {
    const txt = (sn.summary || sn.llm_summary || "").toString();
    if (!txt.trim()) { ChatLogger.toast(false, "Phiên chưa có tóm tắt"); return; }
    const wrapped = "Context từ phiên cũ (" + (sn.started_at || "").slice(0, 16) + "):\n\n" + txt;
    ChatLogger.insertIntoChat(wrapped);
  }

  static injectFull(sessionId) {
    ChatLogger.toast(true, "Đang lấy full transcript để chèn...");
    ChatLogger._pendingInjectFull = sessionId;
    ChatLogger.triggerFetchDetail(sessionId);
  }

  static openDetailModal(sessionId) {
    let m = document.getElementById("cl-hist-modal");
    if (m) m.remove();
    m = document.createElement("div");
    m.id = "cl-hist-modal";
    m.style.cssText =
      "position:fixed;inset:0;z-index:2147483647;background:rgba(0,0,0,.5);" +
      "display:flex;align-items:center;justify-content:center;font-family:system-ui,sans-serif;";
    m.innerHTML =
      '<div style="background:#fff;color:#000;width:min(60vw,900px);min-width:480px;max-height:85vh;border-radius:12px;display:flex;flex-direction:column;overflow:hidden">' +
        '<div style="padding:12px 16px;border-bottom:1px solid #eee;display:flex;justify-content:space-between;align-items:center">' +
          '<div><div style="font-size:14px;font-weight:600">📄 Chi tiết phiên</div>' +
          '<div id="cl-modal-meta" style="font-size:11px;color:#888;margin-top:2px">id: ' + ChatLogger._esc(sessionId) + '</div></div>' +
          '<div style="display:flex;gap:8px;align-items:center">' +
            '<span id="cl-modal-inj-sum" style="font-size:11px;padding:4px 10px;background:#e3f2fd;border:1px solid #90caf9;border-radius:6px;color:#1565c0;cursor:pointer">← inject tóm tắt</span>' +
            '<span id="cl-modal-inj-full" style="font-size:11px;padding:4px 10px;background:#fff3e0;border:1px solid #ffb74d;border-radius:6px;color:#e65100;cursor:pointer">⇐ inject full</span>' +
            '<span id="cl-modal-close" style="font-size:22px;color:#888;cursor:pointer;line-height:1">×</span>' +
          '</div>' +
        '</div>' +
        '<div id="cl-modal-body" style="flex:1;overflow-y:auto;padding:12px 16px;font-size:13px;line-height:1.5">Đang tải...</div>' +
        '<div style="padding:7px 16px;border-top:1px solid #eee;background:#fafafa;display:flex;justify-content:space-between;font-size:11px">' +
          '<span id="cl-modal-copy" style="color:#1976d2;cursor:pointer">📋 copy id</span>' +
          '<span style="color:#888">Đóng để về panel</span>' +
        '</div>' +
      '</div>';
    document.body.appendChild(m);
    m.querySelector("#cl-modal-close").onclick = () => m.remove();
    m.onclick = (e) => { if (e.target === m) m.remove(); };
    m.querySelector("#cl-modal-copy").onclick = () => ChatLogger._copyId(sessionId);
    m.querySelector("#cl-modal-inj-sum").onclick = () => {
      if (ChatLogger._modalSession) ChatLogger.injectSummary(ChatLogger._modalSession);
    };
    m.querySelector("#cl-modal-inj-full").onclick = () => ChatLogger.injectFull(sessionId);
    ChatLogger._modalMode = true;
    ChatLogger._modalSessionId = sessionId;
    ChatLogger.triggerFetchDetail(sessionId);
  }

  static renderDetailInModal(session) {
    const body = document.getElementById("cl-modal-body");
    const meta = document.getElementById("cl-modal-meta");
    if (!body) return;
    ChatLogger._modalSession = session;
    let tr = session.transcript;
    if (typeof tr === "string") { try { tr = JSON.parse(tr); } catch (e) {} }
    if (!Array.isArray(tr)) tr = [];
    if (meta) {
      const when = (session.started_at || "").toString().slice(0, 16).replace("T", " ");
      const cnt = tr.length || session.message_count || 0;
      const tokEst = Math.round((JSON.stringify(tr).length || 0) / 4);
      meta.innerHTML = ChatLogger._esc(session.id || "?") + ' · ' + when + ' · ' + cnt + ' tin · ~' + tokEst + ' tok';
    }
    if (tr.length === 0) {
      body.innerHTML = '<div style="text-align:center;color:#888;padding:20px">Phiên này không có nội dung.</div>';
      return;
    }
    body.innerHTML = "";
    tr.forEach((msg) => {
      const role = msg.role || msg.author || "?";
      const text = ChatLogger._fixMojibake((msg.content || msg.text || "").toString());
      const cap = msg.captured_at ? new Date(msg.captured_at * 1000).toISOString().slice(11, 19) : "";
      const bg = role === "user" ? "#e3f2fd" : (role === "assistant" ? "#e8f5e9" : "#f5f5f5");
      const color = role === "user" ? "#1565c0" : (role === "assistant" ? "#2e7d32" : "#555");
      const row = document.createElement("div");
      row.style.cssText = "margin-bottom:12px";
      row.innerHTML =
        '<div style="display:flex;align-items:center;gap:6px;margin-bottom:4px">' +
          '<span style="width:22px;height:22px;border-radius:50%;background:' + bg + ';color:' + color +
            ';display:inline-flex;align-items:center;justify-content:center;font-size:10px;font-weight:600">' +
            ChatLogger._esc(role.charAt(0).toUpperCase()) + '</span>' +
          '<span style="font-size:11px;font-weight:500">' + ChatLogger._esc(role) + '</span>' +
          (cap ? '<span style="font-size:10px;color:#999">' + cap + '</span>' : '') +
        '</div>' +
        '<div style="margin-left:30px;white-space:pre-wrap;word-break:break-word">' + ChatLogger._esc(text) + '</div>';
      body.appendChild(row);
    });
  }

    static triggerCompact() {
    try {
      if (ChatLogger.emitMethod === "event") {
        window.__TAURI__.event.emit("chat-logger://compact", {});
      } else if (ChatLogger.emitMethod === "internals") {
        window.__TAURI_INTERNALS__.postMessage({ cmd: "compact_session" });
      } else if (ChatLogger.emitMethod === "invoke") {
        window.__TAURI__.core.invoke("compact_session");
      }
    } catch (err) {
      console.error("[chat-logger] compact trigger failed:", err);
    }
  }

  static send(id, conversationId, role, content) {
    ChatLogger.loggedIds.add(id);
    const payload = { id, conversationId, role, content };
    try {
      if (ChatLogger.emitMethod === "event") {
        window.__TAURI__.event.emit("chat-logger://log-message", payload);
      } else if (ChatLogger.emitMethod === "internals") {
        window.__TAURI_INTERNALS__.postMessage({ cmd: "log_message", ...payload });
      } else if (ChatLogger.emitMethod === "invoke") {
        window.__TAURI__.core.invoke("log_message", payload);
      }
    } catch (err) {
      ChatLogger.loggedIds.delete(id);
      console.error("[chat-logger] send failed:", err);
    }
  }

  static compact() { ChatLogger.triggerCompact(); }
}

window.addEventListener("DOMContentLoaded", ChatLogger.start);
window.addEventListener("popstate", () => setTimeout(ChatLogger.start, 500));
window.ChatLogger = ChatLogger;
