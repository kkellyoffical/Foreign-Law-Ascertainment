/* 对话模块：消息流渲染、SSE 流式、工具指示、引用来源 */
const Chat = {
  sending: false,
  mode: "fast",

  mount(main) {
    main.innerHTML = `
      <div class="topbar">
        <div><div class="topbar-title">${Icons.scale} 域外法查明助手</div></div>
        <div class="topbar-spacer"></div>
        <div class="topbar-sub"><span class="live-dot"></span> 检索增强 · 双语成文法底座 · 判例库建设中</div>
        <button class="btn btn-ghost" id="export-pdf-btn" title="将本会话导出为规范的《域外法查明意见书》PDF" style="margin-left:10px;font-size:12.5px;white-space:nowrap">${Icons.doc} 导出意见书</button>
        <button class="btn btn-ghost" id="export-docx-btn" title="导出 Word 版查明意见书" style="font-size:12.5px;white-space:nowrap">Word</button>
      </div>
      <div class="chat-stream" id="chat-stream"><div class="stream-inner" id="stream-inner"></div></div>
      <div class="composer-wrap">
        <div class="composer-modes">
          <span class="modes-label">查明模式</span>
          <div class="mode-seg">
            <button class="mode-btn" data-mode="fast">标准</button>
            <button class="mode-btn" data-mode="deep">深度研究</button>
          </div>
        </div>
        <div class="composer">
          <textarea id="composer-input" rows="1" placeholder="输入巴西法律查明问题，例如：巴西法下无正当事由解雇（dispensa sem justa causa）的补偿义务如何规定？"></textarea>
          <button class="send-btn" id="send-btn" title="发送">${Icons.send}</button>
        </div>
        <div class="composer-hint">标准模式响应快｜深度研究更全面（复杂问题约 1–3 分钟）· Enter 发送，Shift+Enter 换行 · 结论仅供研究参考，请核对原始出处</div>
      </div>`;

    const input = main.querySelector("#composer-input");
    input.addEventListener("input", () => {
      input.style.height = "auto";
      input.style.height = Math.min(input.scrollHeight, 200) + "px";
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); Chat.send(); }
    });
    main.querySelector("#send-btn").addEventListener("click", () => Chat.send());
    // 导出查明意见书：弹窗可填委托方/承办人/事项编号（V1.3-B4.5，记忆上次填写）
    const doExport = (format) => {
      const id = App.state.currentConvId;
      if (!id) { flash("请先完成一次查明后再导出意见书", true); return; }
      const saved = JSON.parse(localStorage.getItem("za_report_meta") || "{}");
      const { root, close } = openModal(`
        <h3>导出查明意见书（${format === "docx" ? "Word" : "PDF"}）</h3>
        <p class="muted" style="font-size:12.5px;margin:4px 0 12px">以下为可选署名信息，将印在意见书标题下方；留空则不显示。</p>
        <div class="field"><label>委托方（可选）</label><input id="rm-client" maxlength="60" value="${esc(saved.client_name || "")}" placeholder="如：××（上海）投资有限公司"></div>
        <div class="field"><label>承办人（可选）</label><input id="rm-handler" maxlength="60" value="${esc(saved.handler_name || "")}" placeholder="如：张律师"></div>
        <div class="field"><label>委托事项编号（可选）</label><input id="rm-ref" maxlength="60" value="${esc(saved.matter_ref || "")}" placeholder="如：2026-委-018"></div>
        <button class="btn btn-primary" id="rm-go" style="width:100%;margin-top:6px">生成并下载</button>`);
      root.querySelector("#rm-go").addEventListener("click", async () => {
        const meta = {
          client_name: root.querySelector("#rm-client").value.trim(),
          handler_name: root.querySelector("#rm-handler").value.trim(),
          matter_ref: root.querySelector("#rm-ref").value.trim(),
        };
        localStorage.setItem("za_report_meta", JSON.stringify(meta));
        const btn = root.querySelector("#rm-go");
        btn.disabled = true; btn.textContent = "生成中…（约 10 秒）";
        try { await API.downloadReport(id, format, meta); close(); }
        catch (e) { btn.disabled = false; btn.textContent = "生成并下载"; flash(e.message || "导出失败", true); }
      });
    };
    main.querySelector("#export-pdf-btn").addEventListener("click", () => doExport("pdf"));
    main.querySelector("#export-docx-btn").addEventListener("click", () => doExport("docx"));
    // 模式切换（深度 / 快速），记忆到 localStorage
    Chat.mode = localStorage.getItem("za_chat_mode") || "fast";
    main.querySelectorAll(".mode-btn").forEach((bt) => {
      bt.classList.toggle("active", bt.dataset.mode === Chat.mode);
      bt.addEventListener("click", () => {
        Chat.mode = bt.dataset.mode;
        localStorage.setItem("za_chat_mode", Chat.mode);
        main.querySelectorAll(".mode-btn").forEach((x) => x.classList.toggle("active", x.dataset.mode === Chat.mode));
      });
    });
    setTimeout(() => input.focus(), 0);
  },

  showWelcome() {
    const inner = document.getElementById("stream-inner");
    if (!inner) return;
    const suggestions = [
      ["劳动用工", Icons.gavel, "巴西法下无正当事由解雇（dispensa sem justa causa）时雇主的补偿义务有哪些？"],
      ["民事责任", Icons.shield, "巴西民法典下精神损害赔偿（dano moral）的构成要件与责任基础是什么？"],
      ["国际仲裁", Icons.book, "外国仲裁裁决在巴西的承认与执行需满足哪些条件？"],
      ["数据合规", Icons.users, "LGPD 对个人数据跨境传输规定了哪些合法路径与条件？"],
    ];
    inner.innerHTML = `
      <div class="welcome">
        <div class="welcome-emblem">${Icons.logo}</div>
        <h2>${esc(App.state.config.platform_name)}</h2>
        <div class="welcome-univ">华东政法大学 · 涉外法治实验室</div>
        <p>提出任何巴西法律查明问题，智能体将自主检索权威底座、多轮取证，给出附完整出处的可审查查明意见。</p>
        <div class="welcome-bases">
          <span class="base-chip bc-gold">${Icons.gavel} 官方葡语核心法 42 部</span>
          <span class="base-chip bc-indigo">${Icons.doc} 英译联邦立法 20,593 件</span>
          <span class="base-chip bc-teal">${Icons.scale} CF/88 + 六法典 + CLT/CDC/LGPD</span>
          <span class="base-chip bc-violet">${Icons.book} 判例库（STF/STJ）建设中</span>
        </div>
        <div class="suggestions">
          ${suggestions.map((s) => `<button class="suggestion" data-q="${esc(s[2])}"><b>${s[1]}${esc(s[0])}</b><span>${esc(s[2])}</span></button>`).join("")}
        </div>
      </div>`;
    inner.querySelectorAll(".suggestion").forEach((b) =>
      b.addEventListener("click", () => {
        const input = document.getElementById("composer-input");
        input.value = b.dataset.q;
        Chat.send();
      })
    );
  },

  async load(convId) {
    const inner = document.getElementById("stream-inner");
    if (!inner) return;
    inner.innerHTML = `<div class="loading-center"><span class="spin-inline"></span> 加载会话…</div>`;
    try {
      const r = await API.getConversation(convId);
      inner.innerHTML = "";
      if (!r.messages.length) { Chat.showWelcome(); return; }
      r.messages.forEach((m) => {
        Chat.renderMessage(m.role, m.content, m.citations, m.verification, m.tool_trace);
      });
      Chat.scrollBottom();
    } catch (e) {
      inner.innerHTML = `<div class="loading-center">加载失败：${esc(e.message)}</div>`;
    }
  },

  renderMessage(role, content, citations, verification, toolTrace) {
    const inner = document.getElementById("stream-inner");
    const u = App.state.user;
    const isU = role === "user";
    const msg = h(`
      <div class="msg ${role}">
        <div class="msg-avatar">${isU ? esc((u.display_name || u.username)[0].toUpperCase()) : Icons.scale}</div>
        <div class="msg-body">
          <div class="msg-role">${isU ? "你" : "查明助手"}</div>
          <div class="msg-content">${isU ? esc(content).replace(/\n/g, "<br>") : md(content)}</div>
        </div>
      </div>`);
    inner.appendChild(msg);
    const body = msg.querySelector(".msg-body");
    // V1.3-B4.3：历史消息也渲染取证轨迹（tool_trace 已落库，重开会话仍可审查取证过程）
    if (!isU && toolTrace && toolTrace.length) {
      const el = Chat.traceHistoryEl(toolTrace);
      if (el) body.insertBefore(el, body.querySelector(".msg-content"));
    }
    if (!isU && citations && citations.length) {
      body.appendChild(Chat.citationsEl(citations));
    }
    if (!isU && verification && verification.summary && verification.summary.total) {
      body.appendChild(Chat.verificationEl(verification));
    }
    return msg;
  },

  /* 历史取证轨迹（折叠）：由落库的 tool_trace 还原 */
  traceHistoryEl(trace) {
    const TOOL_META = {
      search_statutes: "检索成文法", search_regulations: "检索公告规章",
      search_guidance: "检索监管指南", search_treaties: "检索条约",
      search_cases: "判例检索", get_case_detail: "判例详情",
      get_statute_detail: "调取法条详情", search_amendments: "检索修订沿革",
      find_cases_applying_statute: "法条反查判例", lookup_case: "案名溯源",
    };
    const rows = (trace || []).filter((t) => t.tool);
    if (!rows.length) return null;
    const el = h(`
      <div class="trace">
        <div class="trace-head">${Icons.check}<span class="trace-label">已综合 ${rows.length} 轮检索取证（点击展开轨迹）</span><span class="trace-caret">▼</span></div>
        <div class="trace-rows"></div>
      </div>`);
    const box = el.querySelector(".trace-rows");
    rows.forEach((t) => {
      const lbl = TOOL_META[t.tool] || t.tool;
      const q = t.query || t.act_title || t.case_name || (t.document_id ? "id=" + t.document_id : "") || (t.section_id ? "id=" + t.section_id : "");
      box.appendChild(h(`<div class="trace-row"><b>${esc(lbl)}</b><span class="tq">${esc(String(q))}</span><span class="tr-state done">✓</span></div>`));
    });
    el.querySelector(".trace-head").addEventListener("click", () => el.classList.toggle("open"));
    return el;
  },

  /* 引用核验条：✅已核验 / ⚠部分 / ❌未核验，点击展开非全核验项明细 */
  verificationEl(v) {
    const s = v.summary || {};
    const issues = (v.items || []).filter((i) => i.status !== "verified");
    const parts = [`<b>✅ 已核验 ${s.verified || 0}</b>`];
    if (s.partial) parts.push(`<b style="color:#b45309">⚠ 部分核验 ${s.partial}</b>`);
    if (s.declared) parts.push(`<b style="color:#6b7280">🏷 已声明未核验 ${s.declared}</b>`);
    if (s.unverified) parts.push(`<b style="color:#b91c1c">❌ 未核验 ${s.unverified}</b>`);
    const box = h(`
      <div class="verify-strip${issues.length ? " has-issues" : ""}">
        <span class="vs-head">🛡 引用核验（机器验真）：${parts.join(" · ")}${issues.length ? ' <span class="vs-caret">▸ 明细</span>' : ""}</span>
        <div class="vs-detail" style="display:none"></div>
      </div>`);
    if (issues.length) {
      const detail = box.querySelector(".vs-detail");
      detail.innerHTML = issues.map((i) => {
        const mk = i.status === "partial" ? ["vs-partial", "⚠"] : i.status === "declared" ? ["vs-partial", "🏷"] : ["vs-bad", "❌"];
        return `<div class="vs-row"><span class="${mk[0]}">${mk[1]}</span> <code>${esc(i.ref)}</code> — ${esc(i.note || "")}</div>`;
      }).join("");
      box.querySelector(".vs-head").addEventListener("click", () => {
        const open = detail.style.display !== "none";
        detail.style.display = open ? "none" : "block";
        box.querySelector(".vs-caret").textContent = open ? "▸ 明细" : "▾ 收起";
      });
      box.querySelector(".vs-head").style.cursor = "pointer";
    }
    return box;
  },

  citationsEl(citations) {
    const box = h(`<div class="citations"><div class="citations-title">${Icons.doc} 引用来源（${citations.length}）</div><div class="citation-grid"></div></div>`);
    const grid = box.querySelector(".citation-grid");
    citations.forEach((c) => {
      let name, meta;
      const official = { regulation: ["公告规章", "cite-tag-regulation"], guidance: ["监管指南", "cite-tag-guidance"], treaty: ["条约", "cite-tag-treaty"] };
      if (c.type === "statute") {
        name = `<span class="cite-tag cite-tag-statute">成文法</span>${esc(c.title || "法规")}`;
        meta = `Act ${esc(c.act_number || "")}${c.act_year ? "/" + esc(c.act_year) : ""} · §${esc(c.section_label || "")}${c.heading ? " " + esc(c.heading) : ""}`;
      } else if (official[c.type]) {
        const [label, cls] = official[c.type];
        name = `<span class="cite-tag ${cls}">${label}</span>${esc(c.title || "官方材料")}`;
        meta = `${esc(c.document_type || c.notice_type || "Notice")}${c.gazette_number ? " · Gazette " + esc(c.gazette_number) : ""}${c.source_authority ? " · " + esc(c.source_authority) : ""}${c.publication_date ? " · " + esc(c.publication_date) : ""}`;
      } else {
        name = `<span class="cite-tag cite-tag-case">判例</span>${esc(c.case_name || "案例 " + c.document_id)}`;
        meta = `${esc(c.court || c.court_level || "")}${c.decision_date ? " · " + esc(c.decision_date) : ""}`;
      }
      const card = h(`<div class="citation-card"><div class="citation-name">${name}</div><div class="citation-meta">${meta}</div></div>`);
      card.addEventListener("click", () =>
        c.type === "statute" ? Chat.showStatuteCitation(c)
        : official[c.type] ? Chat.showRegulationCitation(c)
        : Chat.showCitation(c));
      grid.appendChild(card);
    });
    return box;
  },

  showCitation(c) {
    const { root } = openModal(`
      <h3 style="font-size:16px;line-height:1.4">${esc(c.case_name || "案例 " + c.document_id)}</h3>
      <div class="kv" style="margin-bottom:16px">
        <span class="k">document_id</span><span>${esc(c.document_id)}</span>
        <span class="k">法院</span><span>${esc(c.court || "—")}</span>
        <span class="k">级别</span><span>${esc(c.court_level || "—")}</span>
        <span class="k">日期</span><span>${esc(c.decision_date || "—")}</span>
        <span class="k">案号</span><span>${esc(c.case_number || "—")}</span>
        <span class="k">引证</span><span>${esc(c.citation || "—")}</span>
        <span class="k">案由</span><span>${esc(c.case_nature || "—")}</span>
      </div>
      <button class="btn btn-primary" id="cite-ft-btn" style="width:100%">${Icons.doc} 查看判决原文</button>
      <div id="cite-ft-box"></div>`);
    const btn = root.querySelector("#cite-ft-btn");
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      btn.textContent = "加载原文中…";
      try {
        const r = await API.caseFulltext(c.document_id);
        root.querySelector("#cite-ft-box").innerHTML =
          `<div class="fulltext-view">${esc(r.case.full_text || "（暂无原文）")}</div>`;
        btn.style.display = "none";
      } catch (e) {
        btn.disabled = false;
        btn.textContent = "加载失败，点此重试";
      }
    });
  },

  showStatuteCitation(c) {
    openModal(`
      <h3 style="font-size:16px;line-height:1.4">${esc(c.title || "法规")}</h3>
      <div class="kv" style="margin-bottom:14px">
        <span class="k">Act 编号</span><span>${esc(c.act_number || "—")}${c.act_year ? " / " + esc(c.act_year) : ""}</span>
        <span class="k">条文</span><span>§${esc(c.section_label || "—")}${c.heading ? " · " + esc(c.heading) : ""}</span>
        <span class="k">公布日期</span><span>${esc(c.publication_date || "—")}</span>
        <span class="k">Gazette</span><span>${esc(c.gazette_number || "—")}</span>
      </div>
      <div class="fulltext-view">${esc(c.section_text || "（无条文文本）")}</div>
      ${/^https?:\/\//i.test(c.source_url || "") ? `<div style="margin-top:10px"><a href="${esc(c.source_url)}" target="_blank" rel="noopener noreferrer" class="muted" style="font-size:12px">官方出处 ↗</a></div>` : ""}`);
  },

  showRegulationCitation(c) {
    openModal(`
      <h3 style="font-size:16px;line-height:1.4">${esc(c.title || "官方公告")}</h3>
      <div class="kv" style="margin-bottom:14px">
        <span class="k">文件类型</span><span>${esc(c.document_type || c.notice_type || "—")}</span>
        <span class="k">公报号</span><span>${esc(c.gazette_number || "—")}</span>
        <span class="k">发布日期</span><span>${esc(c.publication_date || "—")}</span>
        <span class="k">来源机构</span><span>${esc(c.source_authority || "—")}</span>
      </div>
      <div class="fulltext-view">${esc(c.chunk_text || "（无正文）")}</div>
      ${/^https?:\/\//i.test(c.source_url || "") ? `<div style="margin-top:10px"><a href="${esc(c.source_url)}" target="_blank" rel="noopener noreferrer" class="muted" style="font-size:12px">官方出处 ↗</a></div>` : ""}`);
  },

  scrollBottom() {
    const s = document.getElementById("chat-stream");
    if (s) s.scrollTop = s.scrollHeight;
  },

  async send() {
    if (Chat.sending) return;
    const input = document.getElementById("composer-input");
    const text = (input.value || "").trim();
    if (!text) return;
    Chat.sending = true;
    input.value = "";
    input.style.height = "auto";
    const sendBtn = document.getElementById("send-btn");
    // V1.3-B4.1 停止生成：发送键在生成期间变为停止键（后端断开即停，已生成部分照常落库）
    const ctl = new AbortController();
    Chat._abort = ctl;
    sendBtn.classList.add("stop-mode");
    sendBtn.title = "停止生成";
    sendBtn.innerHTML = "⏹";
    const restoreBtn = () => {
      sendBtn.classList.remove("stop-mode");
      sendBtn.title = "发送";
      sendBtn.innerHTML = Icons.send;
      sendBtn.disabled = false;
    };
    sendBtn.onclick = () => { if (Chat.sending) ctl.abort(); };

    // 清除欢迎页
    const inner = document.getElementById("stream-inner");
    if (inner.querySelector(".welcome")) inner.innerHTML = "";

    try {
      // 必要时先创建会话
      if (!App.state.currentConvId) {
        const r = await API.createConversation(null);
        App.state.currentConvId = r.conversation.id;
        App.state.conversations.unshift(r.conversation);
        App.renderConvList();
      }
      const convId = App.state.currentConvId;
      Chat.renderMessage("user", text, null);
      Chat.scrollBottom();

      // 助手占位（含取证轨迹时间线）
      const msg = h(`
        <div class="msg assistant">
          <div class="msg-avatar">${Icons.scale}</div>
          <div class="msg-body"><div class="msg-role">查明助手</div>
            <div class="trace open" id="cur-trace" style="display:none">
              <div class="trace-head"><span class="spinner"></span><span class="trace-label">正在检索取证…</span><span class="trace-caret">▼</span></div>
              <div class="trace-rows"></div>
            </div>
            <div class="msg-content cursor-blink" id="cur-content"></div>
          </div>
        </div>`);
      inner.appendChild(msg);
      Chat.scrollBottom();

      const traceEl = msg.querySelector("#cur-trace");
      const traceRows = traceEl.querySelector(".trace-rows");
      const traceLabel = traceEl.querySelector(".trace-label");
      const traceHead = traceEl.querySelector(".trace-head");
      traceHead.addEventListener("click", () => traceEl.classList.toggle("open"));
      const contentEl = msg.querySelector("#cur-content");
      let raw = "", pending = false, toolCount = 0, firstDelta = true, runningRow = null;

      const TOOL_META = {
        search_statutes: [Icons.book, "检索成文法"],
        search_regulations: [Icons.megaphone, "检索公告规章"],
        search_guidance: [Icons.compass, "检索监管指南"],
        search_treaties: [Icons.globe, "检索条约"],
        search_cases: [Icons.gavel, "判例检索"],
        get_case_detail: [Icons.doc, "判例详情"],
        get_statute_detail: [Icons.doc, "调取法条详情"],
        find_cases_applying_statute: [Icons.book, "法条反查判例"],
        lookup_case: [Icons.search, "案名溯源"],
      };
      const finishRunning = () => {
        if (runningRow) {
          runningRow.querySelector(".tr-state").className = "tr-state done";
          runningRow.querySelector(".tr-state").textContent = "✓";
          runningRow = null;
        }
      };

      const flushDelta = () => {
        contentEl.innerHTML = md(raw);
        pending = false;
        Chat.scrollBottom();
      };

      await API.chatStream(convId, text, (event, data) => {
        if (event === "tool") {
          if (data.status === "calling") {
            toolCount++;
            finishRunning();
            const [ico, lbl] = TOOL_META[data.name] || [Icons.search, "检索资料"];
            traceEl.style.display = "block";
            traceLabel.textContent = `正在检索取证（第 ${toolCount} 轮）`;
            runningRow = h(`<div class="trace-row">${ico}<b>${lbl}</b><span class="tq">${esc(data.query || "")}</span><span class="tr-state running"></span></div>`);
            traceRows.appendChild(runningRow);
            traceRows.scrollTop = traceRows.scrollHeight;
            Chat.scrollBottom();
          } else if (data.status === "done") {
            finishRunning();
          }
        } else if (event === "delta") {
          if (firstDelta) {
            firstDelta = false;
            finishRunning();
            if (toolCount > 0) {
              traceEl.classList.remove("open");
              traceHead.querySelector(".spinner").remove();
              traceHead.insertAdjacentHTML("afterbegin", Icons.check);
              traceLabel.textContent = `已完成 ${toolCount} 轮检索，正在综合作答…`;
            } else traceEl.style.display = "none";
          }
          raw += data.text || "";
          if (!pending) { pending = true; requestAnimationFrame(flushDelta); }
        } else if (event === "final") {
          raw = raw || "";
          contentEl.classList.remove("cursor-blink");
          contentEl.innerHTML = md(raw);
          if (data.citations && data.citations.length) {
            msg.querySelector(".msg-body").appendChild(Chat.citationsEl(data.citations));
          }
          if (toolCount > 0) traceLabel.textContent = `已综合 ${toolCount} 轮检索取证（点击展开轨迹）`;
          else traceEl.style.display = "none";
          Chat.scrollBottom();
        } else if (event === "verification") {
          if (data.summary && data.summary.total) {
            msg.querySelector(".msg-body").appendChild(Chat.verificationEl(data));
            Chat.scrollBottom();
          }
        } else if (event === "error") {
          contentEl.classList.remove("cursor-blink");
          contentEl.innerHTML = `<div style="color:var(--danger)">⚠ ${esc(data.error || "生成失败")}</div>`;
        }
      }, ctl.signal, Chat.mode);
      contentEl.classList.remove("cursor-blink");
      // 刷新会话列表（标题/排序）
      App.refreshConversations();
    } catch (e) {
      if (e && e.name === "AbortError") {
        flash("已停止生成（已生成部分将保存）");
        const cc = document.getElementById("cur-content");
        if (cc) cc.classList.remove("cursor-blink");
        setTimeout(() => { if (App.state.currentConvId) Chat.load(App.state.currentConvId); }, 1200);
      } else {
        flash(e.message, true);
        const cc = document.getElementById("cur-content");
        if (cc) { cc.classList.remove("cursor-blink"); if (!cc.innerHTML) cc.innerHTML = `<div style="color:var(--danger)">⚠ ${esc(e.message)}</div>`; }
      }
    } finally {
      Chat.sending = false;
      Chat._abort = null;
      const sb = document.getElementById("send-btn");
      if (sb) { sb.onclick = null; restoreBtn(); }
    }
  },
};
