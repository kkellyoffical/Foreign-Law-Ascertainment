/* 管理后台：泰国成文法数据概览、用户、邀请与质量管理 */

const Admin = {
  tab: "overview",
  statuteState: { q: "" },

  mount(main) {
    main.innerHTML = `
      <div class="topbar"><div class="topbar-title">${Icons.gauge} 管理控制台</div><div class="topbar-spacer"></div><div class="topbar-sub"><span class="live-dot"></span> 仅管理员可见 · 操作留痕</div></div>
      <div class="admin-page"><div class="admin-inner">
        <div class="admin-security-banner" role="alert">
          <span class="asb-icon">${Icons.warning}</span>
          <span class="asb-text"><b>受限区域 · RESTRICTED ACCESS</b> —— 本管理面板仅限经授权的管理员访问。系统已启用访问审计：本页面的所有操作、请求与数据访问均被<b>记录、留存并审计</b>。未经授权的访问、探测、扫描或数据导出属违规行为，相关行为指纹将被追溯。继续操作即表示你已获得授权并同意上述监控。</span>
        </div>
        <div class="admin-tabs">
          <button class="admin-tab" data-tab="overview">${Icons.chart} 数据驾驶舱</button>
          <button class="admin-tab" data-tab="users">${Icons.users} 用户与邀请</button>
          <button class="admin-tab" data-tab="statutes">${Icons.book} 成文法检索</button>
          <button class="admin-tab" data-tab="quality">${Icons.shield} 质量与告警</button>
        </div>
        <div id="admin-content"></div>
      </div></div>`;
    main.querySelectorAll(".admin-tab").forEach((t) =>
      t.addEventListener("click", () => { Admin.tab = t.dataset.tab; Admin.renderTabs(); })
    );
    Admin.renderTabs();
  },

  renderTabs() {
    document.querySelectorAll(".admin-tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === Admin.tab));
    if (Admin.tab === "overview") Admin.renderOverview();
    else if (Admin.tab === "users") Admin.renderUsers();
    else if (Admin.tab === "statutes") Admin.renderStatutes();
    else if (Admin.tab === "quality") Admin.renderQuality();
    else Admin.renderOverview();
  },

  /* ---------- 质量与告警（V1.3-B5.1）---------- */
  async renderQuality() {
    const box = document.getElementById("admin-content");
    box.innerHTML = `<div class="skeleton" style="height:120px;margin-bottom:16px"></div><div class="skeleton" style="height:300px"></div>`;
    let q, alerts;
    try {
      [q, alerts] = await Promise.all([API.adminQuality(), API.adminAlerts(30)]);
    } catch (e) { box.innerHTML = `<div class="conv-empty">加载失败：${esc(e.message)}</div>`; return; }
    const t = q.totals || {};
    const tot = (+t.verified || 0) + (+t.partial || 0) + (+t.declared || 0) + (+t.unverified || 0);
    const rate = tot ? ((+t.unverified || 0) / tot * 100).toFixed(1) : "0.0";
    box.innerHTML = `
      <div class="stat-grid" style="display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:18px">
        <div class="stat-card"><div class="sc-v" style="color:#0f766e">${esc(t.verified || 0)}</div><div class="sc-k">✅ 已核验引用</div></div>
        <div class="stat-card"><div class="sc-v" style="color:#b45309">${esc(t.partial || 0)}</div><div class="sc-k">⚠ 部分核验</div></div>
        <div class="stat-card"><div class="sc-v" style="color:#6b7280">${esc(t.declared || 0)}</div><div class="sc-k">🏷 已声明</div></div>
        <div class="stat-card"><div class="sc-v" style="color:#b91c1c">${esc(t.unverified || 0)}</div><div class="sc-k">❌ 未核验</div></div>
        <div class="stat-card"><div class="sc-v">${rate}%</div><div class="sc-k">未核验率（全量）</div></div>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:18px">
        <div class="admin-card"><h3 style="margin:0 0 10px">📥 被引未收录清单（补库工单队列）</h3>
          <div class="table-scroll" style="max-height:340px;overflow:auto"><table class="admin-table"><thead><tr><th>引用</th><th>类型</th><th>次数</th></tr></thead>
          <tbody>${(q.cited_but_missing || []).map((m) => `<tr><td style="max-width:280px;word-break:break-all">${esc(m.ref)}</td><td>${esc(m.kind)}</td><td>${esc(m.times)}</td></tr>`).join("") || `<tr><td colspan="3" class="muted">暂无——所有引用均可溯源 🎉</td></tr>`}</tbody></table></div></div>
        <div class="admin-card"><h3 style="margin:0 0 10px">🔔 系统告警（近 30 条）</h3>
          <div class="table-scroll" style="max-height:340px;overflow:auto"><table class="admin-table"><thead><tr><th>组件</th><th>消息</th><th>时间</th></tr></thead>
          <tbody>${(alerts.alerts || []).map((a) => `<tr><td>${esc(a.component)}</td><td style="max-width:300px;word-break:break-all">${esc(a.message)}</td><td style="white-space:nowrap">${esc((a.created_at || "").slice(0, 16).replace("T", " "))}</td></tr>`).join("") || `<tr><td colspan="3" class="muted">暂无告警</td></tr>`}</tbody></table></div></div>
      </div>
      <p class="muted" style="font-size:12px;margin-top:12px">核验消息覆盖：${esc(t.messages_verified || 0)} / ${esc(t.messages_total || 0)} 条助手回答 · 未核验≠臆造（多为库外真实名案），清单即定向补库依据。</p>`;
  },

  /* ---------- 数据驾驶舱 ---------- */
  async renderOverview() {
    const box = document.getElementById("admin-content");
    box.innerHTML = `<div class="skeleton" style="height:180px;margin-bottom:18px"></div><div class="skeleton" style="height:220px"></div>`;
    try {
      const s = await API.adminStats();
      const num = (n) => Number(n || 0).toLocaleString("en-US");
      const stat = (s.statutes || {});
      const sections = Number(stat.sections || 0);
      const embedded = Number(stat.embedded_sections || 0);
      const coverage = sections ? (embedded / sections * 100).toFixed(1) : "0.0";

      box.innerHTML = `
        <div class="dash-hero">
          <div class="dh-top">${Icons.database}<span class="dh-title">泰国成文法数据资产</span>
            <span class="dh-badge"><span class="live-dot"></span> 全链路在线</span></div>
          <div class="dh-grid">
            <div class="dh-cell"><div class="v" data-count="${stat.documents || 0}">0</div><div class="k">${Icons.book} 成文法文档</div><div class="sub">法律 · 法令 · 部级条例 · 公告</div></div>
            <div class="dh-cell"><div class="v" data-count="${sections}">0</div><div class="k">${Icons.doc} 条文</div><div class="sub">结构化英文译文</div></div>
            <div class="dh-cell"><div class="v" data-count="${embedded}">0</div><div class="k">${Icons.layers} 已向量化</div><div class="sub">覆盖率 ${coverage}%</div></div>
            <div class="dh-cell"><div class="v" data-count="${s.users || 0}">0</div><div class="k">${Icons.users} 平台账号</div><div class="sub">邀请制准入</div></div>
            <div class="dh-cell"><div class="v" data-count="${s.conversations || 0}">0</div><div class="k">${Icons.chat} 查明会话</div><div class="sub">全过程留痕</div></div>
          </div>
        </div>
        <div class="panel">
          <h3>${Icons.shield} 当前数据口径</h3>
          <div class="panel-sub">质量优先的成文法单底座，不展示或暗示尚未接入的泰国判例能力。</div>
          <div class="ops-grid" style="margin-top:18px">
            <div class="ops-card"><div class="ico-wrap iw-gold">${Icons.book}</div><div><div class="v">20</div><div class="k">官方机构提供 / 托管英译</div></div></div>
            <div class="ops-card"><div class="ico-wrap iw-violet">${Icons.doc}</div><div><div class="v">6</div><div class="k">非官方或镜像译本</div></div></div>
            <div class="ops-card"><div class="ico-wrap iw-teal">${Icons.layers}</div><div><div class="v">B.E. / CE</div><div class="k">佛历与公历双纪年</div></div></div>
            <div class="ops-card"><div class="ico-wrap iw-indigo">${Icons.shield}</div><div><div class="v">100%</div><div class="k">条文向量对账目标</div></div></div>
          </div>
        </div>`;

      box.querySelectorAll("[data-count]").forEach((el) => countUp(el, el.dataset.count));
    } catch (e) {
      box.innerHTML = `<div class="loading-center">加载失败：${esc(e.message)}</div>`;
    }
  },

  /* ---------- 用户与邀请 ---------- */
  async renderUsers() {
    const box = document.getElementById("admin-content");
    box.innerHTML = `<div class="loading-center"><span class="spin-inline"></span> 加载…</div>`;
    try {
      const [uu, ii] = await Promise.all([API.adminUsers(), API.adminInvites()]);
      box.innerHTML = `
        <div class="toolbar"><div class="grow"><b>用户管理</b> <span class="muted">（共 ${uu.users.length} 个账号）</span></div>
          <button class="btn btn-primary btn-sm" id="btn-new-user">${Icons.plus} 创建用户</button></div>
        <table class="data-table"><thead><tr>
          <th>用户名</th><th>姓名</th><th>角色</th><th>状态</th><th>创建者</th><th>最后登录</th><th>操作</th>
        </tr></thead><tbody id="user-rows"></tbody></table>

        <div class="toolbar" style="margin-top:30px"><div class="grow"><b>邀请码</b> <span class="muted">（凭码自助注册查明账号）</span></div>
          <button class="btn btn-primary btn-sm" id="btn-new-invite">${Icons.key} 生成邀请码</button></div>
        <table class="data-table"><thead><tr>
          <th>邀请码</th><th>角色</th><th>备注</th><th>状态</th><th>过期时间</th><th>操作</th>
        </tr></thead><tbody id="invite-rows"></tbody></table>`;

      const ur = box.querySelector("#user-rows");
      uu.users.forEach((u) => {
        const row = h(`<tr>
          <td><b>${esc(u.username)}</b></td><td>${esc(u.display_name || "—")}</td>
          <td><span class="pill ${u.role === "admin" ? "pill-gold" : ""}">${ROLE_LABEL[u.role] || u.role}</span></td>
          <td>${u.is_active ? '<span class="pill pill-green">启用</span>' : '<span class="pill pill-red">禁用</span>'}${u.must_change_password ? ' <span class="pill pill-gray">待改密</span>' : ""}</td>
          <td class="muted">${esc(u.created_by_username || "—")}</td>
          <td class="muted">${u.last_login_at ? esc(u.last_login_at.slice(0, 16).replace("T", " ")) : "从未"}</td>
          <td></td></tr>`);
        const ops = row.lastElementChild;
        if (u.id !== App.state.user.id) {
          const toggle = h(`<button class="btn btn-sm btn-ghost">${u.is_active ? "禁用" : "启用"}</button>`);
          toggle.addEventListener("click", () => Admin.updateUser(u.id, { is_active: !u.is_active }));
          const reset = h(`<button class="btn btn-sm btn-ghost">重置密码</button>`);
          reset.addEventListener("click", () => Admin.resetPassword(u.id, u.username));
          const rlink = h(`<button class="btn btn-sm btn-ghost" title="生成 1 小时有效的一次性重置链接，发给用户自行设置新密码">重置链接</button>`);
          rlink.addEventListener("click", async () => {
            try {
              const r = await API.adminResetLink(u.id);
              const url = location.origin + r.reset_url;
              openModal(`<h3>密码重置链接（${esc(u.username)}）</h3>
                <p class="muted" style="font-size:12.5px">1 小时内有效、设置成功即作废。请通过安全渠道发给用户：</p>
                <div class="fulltext-view" style="word-break:break-all;user-select:all">${esc(url)}</div>`);
            } catch (e) { flash(e.message, true); }
          });
          ops.append(toggle, reset, rlink);
        } else {
          ops.innerHTML = '<span class="muted">本人</span>';
        }
        ur.appendChild(row);
      });

      const ir = box.querySelector("#invite-rows");
      if (!ii.invitations.length) ir.innerHTML = `<tr><td colspan="6" class="muted" style="text-align:center">暂无邀请码</td></tr>`;
      ii.invitations.forEach((iv) => {
        const status = iv.used_by_username
          ? `<span class="pill pill-gray">已用（${esc(iv.used_by_username)}）</span>`
          : iv.is_valid ? '<span class="pill pill-green">有效</span>' : '<span class="pill pill-red">已过期</span>';
        const row = h(`<tr>
          <td><code>${esc(iv.code)}</code></td>
          <td>${ROLE_LABEL[iv.role] || iv.role}</td>
          <td class="muted">${esc(iv.note || "—")}</td>
          <td>${status}</td>
          <td class="muted">${iv.expires_at ? esc(iv.expires_at.slice(0, 16).replace("T", " ")) : "永久"}</td>
          <td></td></tr>`);
        const copyBtn = h(`<button class="btn btn-sm btn-ghost">复制链接</button>`);
        copyBtn.addEventListener("click", () => Admin.copyInvite(iv.code));
        row.lastElementChild.appendChild(copyBtn);
        if (!iv.used_by_username) {
          const del = h(`<button class="btn btn-sm btn-danger">删除</button>`);
          del.addEventListener("click", () => Admin.deleteInvite(iv.id));
          row.lastElementChild.appendChild(del);
        }
        ir.appendChild(row);
      });

      box.querySelector("#btn-new-user").addEventListener("click", () => Admin.newUserModal());
      box.querySelector("#btn-new-invite").addEventListener("click", () => Admin.newInviteModal());
    } catch (e) {
      box.innerHTML = `<div class="loading-center">加载失败：${esc(e.message)}</div>`;
    }
  },

  async updateUser(id, payload) {
    try { await API.adminUpdateUser(id, payload); flash("已更新"); Admin.renderUsers(); }
    catch (e) { flash(e.message, true); }
  },

  resetPassword(id, username) {
    const { root, close } = openModal(`
      <h3>重置「${esc(username)}」的密码</h3>
      <div class="auth-error" id="rp-err" style="display:none"></div>
      <div class="field"><label>新密码（至少 8 位）</label><input id="rp-pwd" type="text" placeholder="可直接告知该用户"></div>
      <div class="modal-foot"><button class="btn" id="rp-cancel">取消</button><button class="btn btn-primary" id="rp-ok">确认重置</button></div>`);
    root.querySelector("#rp-cancel").addEventListener("click", close);
    root.querySelector("#rp-ok").addEventListener("click", async () => {
      const pwd = root.querySelector("#rp-pwd").value;
      if (pwd.length < 8) { const e = root.querySelector("#rp-err"); e.textContent = "至少 8 位"; e.style.display = "block"; return; }
      try { await API.adminUpdateUser(id, { reset_password: pwd }); close(); flash("密码已重置，用户下次登录需改密"); Admin.renderUsers(); }
      catch (e) { const el = root.querySelector("#rp-err"); el.textContent = e.message; el.style.display = "block"; }
    });
  },

  newUserModal() {
    const { root, close } = openModal(`
      <h3>创建用户</h3>
      <div class="auth-error" id="nu-err" style="display:none"></div>
      <div class="field"><label>用户名</label><input id="nu-username" placeholder="登录用户名"></div>
      <div class="field"><label>初始密码（至少 8 位）</label><input id="nu-pwd" type="text" placeholder="告知该用户，首次登录需改密"></div>
      <div class="field"><label>姓名 / 称呼</label><input id="nu-name" placeholder="可选"></div>
      <div class="field"><label>角色</label><select id="nu-role"><option value="researcher">查明用户</option><option value="admin">管理员</option></select></div>
      <div class="modal-foot"><button class="btn" id="nu-cancel">取消</button><button class="btn btn-primary" id="nu-ok">创建</button></div>`);
    const err = (m) => { const e = root.querySelector("#nu-err"); e.textContent = m; e.style.display = "block"; };
    root.querySelector("#nu-cancel").addEventListener("click", close);
    root.querySelector("#nu-ok").addEventListener("click", async () => {
      const username = root.querySelector("#nu-username").value.trim();
      const password = root.querySelector("#nu-pwd").value;
      if (username.length < 3) return err("用户名至少 3 位");
      if (password.length < 8) return err("密码至少 8 位");
      try {
        await API.adminCreateUser({ username, password, role: root.querySelector("#nu-role").value, display_name: root.querySelector("#nu-name").value.trim() || null });
        close(); flash("用户已创建"); Admin.renderUsers();
      } catch (e) { err(e.message); }
    });
  },

  newInviteModal() {
    const { root, close } = openModal(`
      <h3>生成邀请码</h3>
      <div class="field"><label>角色</label><select id="ni-role"><option value="researcher">查明用户</option><option value="admin">管理员</option></select></div>
      <div class="field"><label>备注</label><input id="ni-note" placeholder="如：某律所王律师"></div>
      <div class="field"><label>有效期（天）</label><input id="ni-days" type="number" value="7" min="1" max="365"></div>
      <div class="modal-foot"><button class="btn" id="ni-cancel">取消</button><button class="btn btn-primary" id="ni-ok">生成</button></div>`);
    root.querySelector("#ni-cancel").addEventListener("click", close);
    root.querySelector("#ni-ok").addEventListener("click", async () => {
      try {
        const r = await API.adminCreateInvite({
          role: root.querySelector("#ni-role").value,
          note: root.querySelector("#ni-note").value.trim() || null,
          expires_in_days: parseInt(root.querySelector("#ni-days").value) || 7,
        });
        close();
        Admin.showInviteResult(r.invitation.code);
        Admin.renderUsers();
      } catch (e) { flash(e.message, true); }
    });
  },

  inviteUrl(code) { return location.origin + "/register?code=" + encodeURIComponent(code); },

  showInviteResult(code) {
    const url = Admin.inviteUrl(code);
    const { root } = openModal(`
      <h3>邀请码已生成</h3>
      <div class="field"><label>邀请码</label><input value="${esc(code)}" readonly onclick="this.select()"></div>
      <div class="field"><label>注册链接（发给受邀人）</label><input value="${esc(url)}" readonly onclick="this.select()"></div>
      <div class="modal-foot"><button class="btn btn-primary" id="ir-copy">复制链接</button></div>`);
    root.querySelector("#ir-copy").addEventListener("click", () => { Admin.copyText(url); flash("链接已复制"); });
  },

  copyInvite(code) { Admin.copyText(Admin.inviteUrl(code)); flash("注册链接已复制"); },
  copyText(t) {
    if (navigator.clipboard) navigator.clipboard.writeText(t).catch(() => {});
    else { const ta = document.createElement("textarea"); ta.value = t; document.body.appendChild(ta); ta.select(); document.execCommand("copy"); ta.remove(); }
  },

  async deleteInvite(id) {
    if (!confirm("删除该邀请码？")) return;
    try { await API.adminDeleteInvite(id); flash("已删除"); Admin.renderUsers(); }
    catch (e) { flash(e.message, true); }
  },

  /* ---------- 成文法检索 ---------- */
  renderStatutes() {
    const box = document.getElementById("admin-content");
    box.innerHTML = `
      <div class="toolbar">
        <input type="text" id="st-q" placeholder="成文法语义 / 关键词检索（英文最佳，如 personal data consent、foreign business licence）" value="${esc(Admin.statuteState.q)}">
        <button class="btn btn-primary" id="st-go">检索法条</button>
      </div>
      <div class="muted" style="margin-bottom:14px">基于全部成文法条文的语义 + 关键词混合检索（Acts 全文已向量化），返回条文级结果与官方出处。</div>
      <div id="st-result"><div class="muted" style="padding:24px;text-align:center">输入关键词后点击「检索法条」</div></div>`;
    const run = () => { Admin.statuteState.q = box.querySelector("#st-q").value.trim(); Admin.runStatuteSearch(); };
    box.querySelector("#st-go").addEventListener("click", run);
    box.querySelector("#st-q").addEventListener("keydown", (e) => { if (e.key === "Enter") run(); });
    if (Admin.statuteState.q) Admin.runStatuteSearch();
  },

  async runStatuteSearch() {
    const res = document.getElementById("st-result");
    const q = Admin.statuteState.q;
    if (!res) return;
    if (!q) { res.innerHTML = `<div class="muted" style="padding:24px;text-align:center">请输入检索关键词</div>`; return; }
    res.innerHTML = `<div class="loading-center"><span class="spin-inline"></span> 检索中…</div>`;
    try {
      const d = await API.adminSearchStatutes({ q, limit: 15 });
      const results = d.results || [];
      if (!results.length) { res.innerHTML = `<div class="muted" style="padding:24px;text-align:center">未匹配到法条，换个关键词试试</div>`; return; }
      res.innerHTML = `<div class="muted" style="margin-bottom:12px">返回 <b>${results.length}</b> 条相关法条${d.reranked ? " · 已重排" : ""}</div>` +
        results.map((r) => `
          <div class="statute-card">
            <div class="statute-head">${esc(r.title || "")} <span class="muted" style="font-weight:400">${r.act_year ? " · " + esc(r.act_year) : ""}</span></div>
            <div class="statute-sec">Section ${esc(r.section_label || "")} · ${r.translation_official === true ? "官方机构提供/托管英译" : r.translation_official === false ? "非官方英译" : "英文译本"}</div>
            <div class="statute-text">${esc((r.section_text || r.section_preview || "").slice(0, 800))}${(r.section_text || "").length > 800 ? " …" : ""}</div>
            ${/^https?:\/\//i.test(r.source_url || "") ? `<div style="margin-top:8px"><a href="${esc(r.source_url)}" target="_blank" rel="noopener noreferrer" class="muted" style="font-size:11.5px">公开来源 ↗</a></div>` : ""}
          </div>`).join("");
    } catch (e) {
      res.innerHTML = `<div class="loading-center">检索失败：${esc(e.message)}</div>`;
    }
  },
};
