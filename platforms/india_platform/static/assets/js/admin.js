/* 管理后台：数据概览、用户与邀请管理、案例关键词检索 */
const NATURE_LABELS = {
  civil_commercial: "民商事", criminal: "刑事", labour: "劳动", administrative: "行政",
  constitutional: "宪法", pension: "养老金", family: "家事", property_land: "财产/土地",
  competition: "竞争", tax: "税务", intellectual_property: "知识产权", environmental: "环境",
  mining: "采矿", financial_regulation: "金融监管",
};
const COURT_LABELS = {
  supreme_court: "最高法院", high_court: "高等法院",
  tribunal_or_commission: "裁判所与委员会", district_or_sessions: "地区法院",
  privy_council_federal: "枢密院/联邦法院（历史）",
};
const RELEVANCE_LABELS = { high: "高", medium: "中", low: "低", not_applicable: "不适用" };
const SCENARIO_LABELS = {
  contract_management: "合同管理", public_procurement: "公共采购", corporate_governance: "公司治理",
  labour_employment: "劳动用工", property_land: "财产土地", licensing_permits: "许可执照",
  financial_regulation: "金融监管", pension_funds_employee_benefits: "养老金/员工福利",
  consumer_protection: "消费者保护", competition_antitrust: "竞争/反垄断", anti_corruption: "反腐败",
  tax: "税务", environmental: "环境", workplace_safety: "职业安全", intellectual_property: "知识产权",
  mining: "采矿", data_protection: "数据保护", other: "其他", not_applicable: "不适用",
};
const DOCTYPE_LABELS = { case_full: "完整判决", specialist_decision: "专门裁决" };
const lbl = (map, k) => map[k] || k || "—";

const Admin = {
  tab: "overview",
  caseState: { q: "", case_nature: "", court_level: "", compliance_relevance: "", compliance_scenario: "", document_type: "", page: 1, page_size: 20 },
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
          <button class="admin-tab" data-tab="cases">${Icons.gavel} 判例检索</button>
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
    else Admin.renderCases();
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
    box.innerHTML = `
      <div class="skeleton" style="height:150px;margin-bottom:18px"></div>
      <div style="display:grid;grid-template-columns:7fr 5fr;gap:18px;margin-bottom:18px">
        <div class="skeleton" style="height:260px"></div><div class="skeleton" style="height:260px"></div>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:18px">
        <div class="skeleton" style="height:240px"></div><div class="skeleton" style="height:240px"></div>
      </div>`;
    try {
      const s = await API.adminStats();
      const num = (n) => Number(n || 0).toLocaleString("en-US");
      const of = (k, f) => ((s.officials || {})[k] || {})[f] || 0;
      const stat = (s.statutes || {});
      const PALETTE = ["#b5232a", "#d94f56", "#d4a333", "#8a4b2a", "#7e181e", "#c77d33", "#a8666b", "#5f6c63"];

      // 五大底座（检索分块口径）
      const bases = [
        { label: "官方公告规章", value: of("regulations_notices", "chunks"), color: "#b5232a" },
        { label: "判例检索分块", value: s.retrieval_chunks, color: "#d4a333" },
        { label: "成文法条文", value: stat.sections || 0, color: "#7e181e" },
        { label: "监管机构指南", value: of("regulatory_guidance", "chunks"), color: "#d94f56" },
        { label: "立法沿革", value: of("statute_history", "chunks"), color: "#8a4b2a" },
      ];
      const vectorTotal = bases.reduce((a, b) => a + b.value, 0);
      const donut = donutChart(bases, {
        size: 178, stroke: 24,
        centerTitle: (vectorTotal / 1000).toFixed(1) + "k", centerSub: "向量化分块",
      });

      // 合规相关度 mini 环形
      const relColors = { high: "#15803d", medium: "#d4a333", low: "#aba4a0", not_applicable: "#e0d6cf" };
      const relSegs = (s.by_compliance_relevance || []).map((r) => ({ label: lbl(RELEVANCE_LABELS, r.k), value: r.n, color: relColors[r.k] || "#9aa0b0" }));
      const relDonut = donutChart(relSegs, { size: 132, stroke: 18, centerSub: "合规相关度" });

      box.innerHTML = `
        <div class="dash-hero">
          <div class="dh-top">${Icons.database}<span class="dh-title">数据资产总览 · 判例 + 成文法双底座 V1.1（公告 / 条约建设中）</span>
            <span class="dh-badge"><span class="live-dot"></span> 全链路在线</span></div>
          <div class="dh-grid">
            <div class="dh-cell"><div class="v" data-count="${s.total_cases}">0</div>
              <div class="k">${Icons.gavel} 印度判例</div><div class="sub">结构化标注 · 全文向量化</div></div>
            <div class="dh-cell"><div class="v" data-count="${stat.sections || 0}">0</div>
              <div class="k">${Icons.book} 成文法条文</div><div class="sub">${num(stat.documents)} 部 Acts · 法条级</div></div>
            <div class="dh-cell"><div class="v" data-count="${of("regulations_notices", "chunks")}">0</div>
              <div class="k">${Icons.megaphone} 公告规章分块</div><div class="sub">${num(of("regulations_notices", "documents"))} 份公报文件</div></div>
            <div class="dh-cell"><div class="v" data-count="${of("regulatory_guidance", "chunks")}">0</div>
              <div class="k">${Icons.compass} 监管指南分块</div><div class="sub">${num(of("regulatory_guidance", "documents"))} 份机构指引</div></div>
            <div class="dh-cell"><div class="v" data-count="${vectorTotal}">0</div>
              <div class="k">${Icons.layers} 检索分块合计</div><div class="sub">PG 全文 + Milvus 向量 + 重排</div></div>
          </div>
        </div>

        <div class="dash-row cols-2">
          <div class="panel">
            <h3>${Icons.layers} 检索底座构成</h3>
            <div class="panel-sub">各权威底座的向量化分块占比（Qwen3-Embedding-8B · 4096 维 · HNSW/COSINE）</div>
            <div class="donut-flex">
              ${donut.html}
              <div class="donut-legend">
                ${bases.map((b) => `<div class="legend-row"><span class="legend-dot" style="background:${b.color}"></span>
                  <span class="legend-label">${esc(b.label)}</span><span class="legend-val">${num(b.value)}</span>
                  <span class="legend-pct">${((b.value / vectorTotal) * 100).toFixed(1)}%</span></div>`).join("")}
              </div>
            </div>
          </div>
          <div class="panel">
            <h3>${Icons.users} 平台运营</h3>
            <div class="panel-sub">多用户协作 · 邀请制准入 · 全程审计</div>
            <div class="ops-grid">
              <div class="ops-card"><div class="ico-wrap iw-indigo">${Icons.users}</div><div><div class="v" data-count="${s.users}">0</div><div class="k">平台账号</div></div></div>
              <div class="ops-card"><div class="ico-wrap iw-violet">${Icons.chat}</div><div><div class="v" data-count="${s.conversations}">0</div><div class="k">查明会话</div></div></div>
              <div class="ops-card"><div class="ico-wrap iw-gold">${Icons.book}</div><div><div class="v" data-count="${stat.documents || 0}">0</div><div class="k">成文法 Acts</div></div></div>
              <div class="ops-card"><div class="ico-wrap iw-teal">${Icons.doc}</div><div><div class="v" data-count="${of("regulations_notices", "documents")}">0</div><div class="k">公报文件</div></div></div>
            </div>
            <div style="margin-top:16px;display:flex;align-items:center;gap:16px">
              ${relDonut.html}
              <div class="donut-legend">
                ${relSegs.map((b) => `<div class="legend-row"><span class="legend-dot" style="background:${b.color}"></span>
                  <span class="legend-label">${esc(b.label)}</span><span class="legend-val">${num(b.value)}</span></div>`).join("")}
              </div>
            </div>
          </div>
        </div>

        <div class="dash-row cols-2b">
          <div class="panel">
            <h3>${Icons.chart} 判例 · 法院级别分布</h3>
            <div class="panel-sub">${num(s.total_cases)} 件判例按审级归置</div>
            <div id="bars-court">${barList(s.by_court_level, COURT_LABELS, PALETTE)}</div>
          </div>
          <div class="panel">
            <h3>${Icons.grid} 判例 · 案件性质分布</h3>
            <div class="panel-sub">14 类案由的全量结构化标注</div>
            <div id="bars-nature">${barList(s.by_case_nature, NATURE_LABELS, PALETTE)}</div>
          </div>
        </div>

        <div class="panel">
          <h3>${Icons.compass} 判例 · 合规场景画像（Top 12）</h3>
          <div class="panel-sub">单一判例可命中多个合规场景，用于企业合规检索路由</div>
          <div id="bars-scenario" style="column-count:2;column-gap:34px">${barList((s.by_compliance_scenario || []).slice(0, 12), SCENARIO_LABELS, PALETTE)}</div>
        </div>`;

      // 触发动画：数字滚动 + 环形描边 + 条形生长
      box.querySelectorAll("[data-count]").forEach((el) => countUp(el, el.dataset.count));
      donut.animate(); relDonut.animate();
      animateBars(box);
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

  /* ---------- 案例检索 ---------- */
  renderCases() {
    const box = document.getElementById("admin-content");
    const cs = Admin.caseState;
    const opt = (map, sel) =>
      Object.entries(map).map(([k, v]) => `<option value="${k}" ${k === sel ? "selected" : ""}>${esc(v)}</option>`).join("");
    box.innerHTML = `
      <div class="toolbar">
        <input type="text" id="cs-q" placeholder="关键词检索（英文效果最佳，如 public procurement tender）" value="${esc(cs.q)}">
        <select id="cs-nature"><option value="">案由（全部）</option>${opt(NATURE_LABELS, cs.case_nature)}</select>
        <select id="cs-court"><option value="">法院级别（全部）</option>${opt(COURT_LABELS, cs.court_level)}</select>
        <select id="cs-rel"><option value="">相关度（全部）</option>${opt(RELEVANCE_LABELS, cs.compliance_relevance)}</select>
        <select id="cs-scen"><option value="">合规场景（全部）</option>${opt(SCENARIO_LABELS, cs.compliance_scenario)}</select>
        <select id="cs-doc"><option value="">类型（全部）</option>${opt(DOCTYPE_LABELS, cs.document_type)}</select>
        <button class="btn btn-primary" id="cs-go">检索</button>
      </div>
      <div id="cs-result"><div class="loading-center"><span class="spin-inline"></span> 检索中…</div></div>`;
    const run = () => {
      cs.q = box.querySelector("#cs-q").value.trim();
      cs.case_nature = box.querySelector("#cs-nature").value;
      cs.court_level = box.querySelector("#cs-court").value;
      cs.compliance_relevance = box.querySelector("#cs-rel").value;
      cs.compliance_scenario = box.querySelector("#cs-scen").value;
      cs.document_type = box.querySelector("#cs-doc").value;
      cs.page = 1;
      Admin.runCaseSearch();
    };
    box.querySelector("#cs-go").addEventListener("click", run);
    box.querySelector("#cs-q").addEventListener("keydown", (e) => { if (e.key === "Enter") run(); });
    Admin.runCaseSearch();
  },

  async runCaseSearch() {
    const cs = Admin.caseState;
    const res = document.getElementById("cs-result");
    if (!res) return;
    res.innerHTML = `<div class="loading-center"><span class="spin-inline"></span> 检索中…</div>`;
    try {
      const r = await API.adminSearchCases(cs);
      const pages = Math.max(1, Math.ceil(r.total / cs.page_size));
      let rows = r.results
        .map((c) => `<tr class="row-link" data-id="${c.document_id}">
            <td class="muted">${c.document_id}</td>
            <td><b>${esc((c.case_name || c.title || "—"))}</b></td>
            <td>${esc(lbl(COURT_LABELS, c.court_level))}</td>
            <td class="muted">${esc(c.decision_date || "—")}</td>
            <td class="muted">${esc(c.case_number || "—")}</td>
            <td>${esc(lbl(NATURE_LABELS, c.case_nature))}</td>
            <td>${c.compliance_relevance ? `<span class="pill ${c.compliance_relevance === "high" ? "" : "pill-gray"}">${lbl(RELEVANCE_LABELS, c.compliance_relevance)}</span>` : "—"}</td>
          </tr>`).join("");
      if (!r.results.length) rows = `<tr><td colspan="7" class="muted" style="text-align:center;padding:30px">未匹配到案例，请调整关键词或筛选</td></tr>`;
      res.innerHTML = `
        <div class="muted" style="margin-bottom:10px">共匹配 <b>${r.total.toLocaleString("en-US")}</b> 条 · 第 ${cs.page}/${pages} 页</div>
        <table class="data-table"><thead><tr>
          <th>ID</th><th>案名</th><th>法院级别</th><th>判决日期</th><th>案号</th><th>案由</th><th>相关度</th>
        </tr></thead><tbody>${rows}</tbody></table>
        <div class="pager">
          <button class="btn btn-sm" id="cs-prev" ${cs.page <= 1 ? "disabled" : ""}>← 上一页</button>
          <span>第 ${cs.page} / ${pages} 页</span>
          <button class="btn btn-sm" id="cs-next" ${cs.page >= pages ? "disabled" : ""}>下一页 →</button>
        </div>`;
      res.querySelectorAll(".row-link").forEach((tr) =>
        tr.addEventListener("click", () => Admin.openCaseDetail(tr.dataset.id))
      );
      const prev = res.querySelector("#cs-prev"), next = res.querySelector("#cs-next");
      if (prev) prev.addEventListener("click", () => { cs.page--; Admin.runCaseSearch(); });
      if (next) next.addEventListener("click", () => { cs.page++; Admin.runCaseSearch(); });
    } catch (e) {
      res.innerHTML = `<div class="loading-center">检索失败：${esc(e.message)}</div>`;
    }
  },

  async openCaseDetail(id) {
    const { body } = openDrawer("案例详情");
    try {
      const r = await API.adminCaseDetail(id);
      const c = r.case;
      const sec = (title, val) => (val && String(val).trim() && val !== "[]" && val !== "{}")
        ? `<div class="detail-section"><h4>${title}</h4><div class="val">${esc(typeof val === "string" ? val : Admin.fmtJson(val))}</div></div>` : "";
      body.innerHTML = `
        <div class="detail-section"><h4>基本信息</h4>
          <div class="kv">
            <span class="k">案名</span><span>${esc(c.case_name || c.title || "—")}</span>
            <span class="k">法院</span><span>${esc(c.court || "—")}（${esc(lbl(COURT_LABELS, c.court_level))}）</span>
            <span class="k">判决日期</span><span>${esc(c.decision_date || "—")}</span>
            <span class="k">案号</span><span>${esc(c.case_number || "—")}</span>
            <span class="k">引证</span><span>${esc(c.citation || "—")}</span>
            <span class="k">案由</span><span>${esc(lbl(NATURE_LABELS, c.case_nature))}</span>
            <span class="k">合规相关度</span><span>${esc(lbl(RELEVANCE_LABELS, c.compliance_relevance))}</span>
            <span class="k">document_id</span><span>${esc(c.document_id)}</span>
          </div>
          <div style="margin-top:12px"><button class="btn btn-sm btn-primary" id="dt-ft-btn">${Icons.doc} 查看判决原文（清洗全文）</button></div>
          <div id="dt-ft-box"></div>
        </div>
        ${c.compliance_scenarios ? `<div class="detail-section"><h4>合规场景</h4><div>${(Array.isArray(c.compliance_scenarios) ? c.compliance_scenarios : []).map((s) => `<span class="pill" style="margin:2px">${esc(lbl(SCENARIO_LABELS, s))}</span>`).join("")}</div></div>` : ""}
        ${sec("事实 Facts", c.facts)}
        ${sec("争点 Issues", c.issues)}
        ${sec("规则 Rules", c.rules)}
        ${sec("法院说理 Reasoning", c.reasoning)}
        ${sec("判项 Holding", c.holding)}
        ${sec("处置 Disposition", c.disposition)}
        ${sec("当事人 Parties", c.parties)}
        ${sec("公司 Companies", c.companies)}
        ${sec("监管机构 Regulators", c.regulators)}
        ${sec("法规 Statutes", c.statutes)}
        ${sec("法条 Provisions", c.provisions)}
        ${sec("法官 Judges", c.judges)}`;
      const ftBtn = body.querySelector("#dt-ft-btn");
      if (ftBtn) {
        ftBtn.addEventListener("click", async () => {
          ftBtn.disabled = true;
          ftBtn.textContent = "加载原文中…";
          try {
            const fr = await API.caseFulltext(id);
            body.querySelector("#dt-ft-box").innerHTML =
              `<div class="fulltext-view">${esc(fr.case.full_text || "（暂无原文）")}</div>`;
            ftBtn.style.display = "none";
          } catch (e2) {
            ftBtn.disabled = false;
            ftBtn.textContent = "加载失败，点此重试";
          }
        });
      }
    } catch (e) {
      body.innerHTML = `<div class="loading-center">加载失败：${esc(e.message)}</div>`;
    }
  },

  fmtJson(v) {
    if (Array.isArray(v)) {
      if (!v.length) return "—";
      if (v.every((x) => typeof x === "string")) return v.map((x) => "· " + x).join("\n");
      return v.map((x) => "· " + (typeof x === "string" ? x : JSON.stringify(x, null, 2))).join("\n");
    }
    if (v && typeof v === "object") return JSON.stringify(v, null, 2);
    return String(v);
  },

  /* ---------- 成文法检索 ---------- */
  renderStatutes() {
    const box = document.getElementById("admin-content");
    box.innerHTML = `
      <div class="toolbar">
        <input type="text" id="st-q" placeholder="成文法语义 / 关键词检索（英文最佳，如 director duties、personal information protection）" value="${esc(Admin.statuteState.q)}">
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
            <div class="statute-head">${esc(r.title || "")} <span class="muted" style="font-weight:400">· Act ${esc(r.act_number || "")}${r.act_year ? "/" + esc(r.act_year) : ""}</span></div>
            <div class="statute-sec">§${esc(r.section_label || "")}${r.heading ? " · " + esc(r.heading) : ""}</div>
            <div class="statute-text">${esc((r.section_text || r.section_preview || "").slice(0, 800))}${(r.section_text || "").length > 800 ? " …" : ""}</div>
            ${/^https?:\/\//i.test(r.source_url || "") ? `<div style="margin-top:8px"><a href="${esc(r.source_url)}" target="_blank" rel="noopener noreferrer" class="muted" style="font-size:11.5px">官方出处 ↗</a></div>` : ""}
          </div>`).join("");
    } catch (e) {
      res.innerHTML = `<div class="loading-center">检索失败：${esc(e.message)}</div>`;
    }
  },
};
