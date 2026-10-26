/* 主控：启动、鉴权页、主布局、会话侧栏、通用工具 */

/* ---------- 通用工具（全局） ---------- */
function esc(s) {
  return (s == null ? "" : String(s)).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}
function h(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}
function md(text) {
  try {
    const html = window.marked.parse(text || "", { breaks: true, gfm: true });
    // 安全净化：助手输出/检索语料可能含恶意 HTML，统一过 DOMPurify 白名单，
    // 阻断脚本注入与窃取 token 的 XSS（禁 javascript:/data: 等危险协议与事件属性）。
    if (window.DOMPurify) {
      return window.DOMPurify.sanitize(html, {
        USE_PROFILES: { html: true },
        ALLOW_DATA_ATTR: false,
        ADD_ATTR: ["target", "rel"],
      });
    }
    return html;
  } catch (e) {
    return esc(text).replace(/\n/g, "<br>");
  }
}
function flash(msg, isErr) {
  const el = h(`<div class="flash ${isErr ? "err" : ""}">${esc(msg)}</div>`);
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 2800);
}
function openModal(innerHtml) {
  const mask = h(`<div class="modal-mask"><div class="modal">${innerHtml}</div></div>`);
  document.body.appendChild(mask);
  const close = () => mask.remove();
  mask.addEventListener("click", (e) => { if (e.target === mask) close(); });
  return { root: mask, close };
}
function openDrawer(titleHtml) {
  const mask = h(`<div class="drawer-mask"></div>`);
  const drawer = h(
    `<div class="drawer"><div class="drawer-head"><h3>${titleHtml}</h3><button class="close-x">×</button></div><div class="drawer-body"><div class="loading-center"><span class="spin-inline"></span> 加载中…</div></div></div>`
  );
  document.body.appendChild(mask);
  document.body.appendChild(drawer);
  const close = () => { mask.remove(); drawer.remove(); };
  mask.addEventListener("click", close);
  drawer.querySelector(".close-x").addEventListener("click", close);
  return { body: drawer.querySelector(".drawer-body"), close };
}
const ROLE_LABEL = { admin: "管理员", researcher: "查明用户" };

/* ---------- 主控 ---------- */
const App = {
  state: {
    user: null,
    config: { platform_name: "泰国域外法查明系统", platform_subtitle: "Thailand Foreign-Law Research Platform" },
    conversations: [],
    currentConvId: null,
    view: "chat",
  },

  async boot() {
    try { App.state.config = await API.publicConfig(); } catch (e) {}
    document.title = App.state.config.platform_name;
    const params = new URLSearchParams(location.search);
    const inviteCode = params.get("code");
    // V1.3-B4.7 密码重置链接：/app?reset=<token>
    const resetToken = params.get("reset");
    if (resetToken) { App.renderResetPassword(resetToken); return; }
    if (API.isLoggedIn()) {
      try {
        const r = await API.me();
        App.state.user = r.user;
        await App.renderApp();
        if (r.user.must_change_password) App.openChangePassword(true);
        return;
      } catch (e) { API.setToken(null); }
    }
    App.renderAuth(inviteCode ? "register" : "login", inviteCode);
  },

  /* ----- 鉴权页（左品牌叙事 + 右表单） ----- */
  renderAuth(mode, inviteCode) {
    const cfg = App.state.config;
    const root = document.getElementById("app");
    const isReg = mode === "register";
    root.innerHTML = "";
    const card = h(`
      <div class="auth-screen">
        <div class="auth-hero">
          <img class="ah-univ" src="/assets/img/logo.svg" alt="华东政法大学">
          <div class="ah-logo">${Icons.logo}</div>
          <h1 class="ah-title">${esc(cfg.platform_name)}</h1>
          <div class="ah-sub">${esc(cfg.platform_subtitle || "Thailand Foreign-Law Research Platform")}</div>
          <div class="ah-points">
            <div class="ah-point">${Icons.sparkle}<div><b>智能体驱动的法律查明</b><span>千问大模型自主编排检索：理解中文问题、转写英语检索式、多轮取证后综合作答。</span></div></div>
            <div class="ah-point">${Icons.layers}<div><b>泰国官方法规底座</b><span>5,489 份 OCS 法规文档、90,127 个检索单元，覆盖法律、皇家法令、部级条例与公告。</span></div></div>
            <div class="ah-point">${Icons.shield}<div><b>可审查 · 可溯源</b><span>每个结论都附法律名、Section 条号、译本属性与公开出处，引证经规则逐条核验。</span></div></div>
          </div>
          <div class="ah-stats">
            <div class="ah-stat"><div class="v">5,489<em> 份</em></div><div class="k">法规文档</div></div>
            <div class="ah-stat"><div class="v">90,127</div><div class="k">检索单元</div></div>
            <div class="ah-stat"><div class="v">B.E.<em>/CE</em></div><div class="k">佛历与公历双纪年</div></div>
          </div>
          <div class="ah-foot"><span>华东政法大学 · 涉外法治实验室</span><span>内部受限系统 · 仅限授权用户</span></div>
        </div>
        <div class="auth-side"><div class="auth-card">
          <img class="auth-card-univ" src="/assets/img/logo.svg" alt="华东政法大学">
          <div class="auth-card-logo">${Icons.logo}</div>
          <div class="auth-head">
            <h2>${isReg ? "注册查明账号" : "欢迎回来"}</h2>
            <p>${isReg ? "凭管理员发放的邀请码开通账号" : "登录以继续使用域外法查明服务"}</p>
          </div>
          <div class="auth-error" style="display:none"></div>
          <form class="auth-form">
            ${isReg ? `<div class="field"><label>邀请码</label><input name="invite_code" value="${esc(inviteCode || "")}" placeholder="请输入管理员发放的邀请码" required></div>` : ""}
            <div class="field"><label>用户名</label><input name="username" autocomplete="username" placeholder="${isReg ? "设置登录用户名" : "请输入用户名"}" required></div>
            <div class="field"><label>密码</label><input name="password" type="password" autocomplete="${isReg ? "new-password" : "current-password"}" placeholder="${isReg ? "至少 8 位" : "请输入密码"}" required></div>
            ${isReg ? `<div class="field"><label>姓名 / 称呼（可选）</label><input name="display_name" placeholder="如：张律师"></div>` : ""}
            <button type="submit" class="btn btn-primary auth-submit">${isReg ? "注册并进入" : "登 录"}</button>
          </form>
          <div class="auth-switch">
            ${isReg ? `已有账号？<a id="to-login">返回登录</a>` : `持有邀请码？<a id="to-register">注册查明账号</a>`}
          </div>
          <div class="auth-note">${Icons.lock} 本系统仅服务授权用户的泰国法律查明<br>账号由管理员创建或凭邀请码注册 · 全程访问审计</div>
          <div class="auth-beian">
            <a href="/">返回首页</a> ｜ 华东政法大学 · 涉外法治实验室<br>
            <a href="https://beian.mps.gov.cn/#/query/webSearch?code=31011002007947" target="_blank" rel="noopener noreferrer">沪公网安备31011002007947号</a>
            ｜ <a href="https://beian.miit.gov.cn/" target="_blank" rel="noopener noreferrer">沪ICP备2025117200-3</a>
          </div>
        </div></div>
      </div>`);
    root.appendChild(card);

    const errBox = card.querySelector(".auth-error");
    const showErr = (m) => { errBox.textContent = m; errBox.style.display = "block"; };
    const sw = card.querySelector(isReg ? "#to-login" : "#to-register");
    if (sw) sw.addEventListener("click", () => App.renderAuth(isReg ? "login" : "register", inviteCode));

    card.querySelector(".auth-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const btn = card.querySelector(".auth-submit");
      btn.disabled = true; btn.textContent = "处理中…";
      const fd = new FormData(e.target);
      try {
        let r;
        if (isReg) {
          r = await API.register({
            invite_code: fd.get("invite_code").trim(),
            username: fd.get("username").trim(),
            password: fd.get("password"),
            display_name: (fd.get("display_name") || "").trim() || null,
          });
        } else {
          r = await API.login(fd.get("username").trim(), fd.get("password"));
        }
        API.setToken(r.token);
        App.state.user = r.user;
        await App.renderApp();
        if (r.user.must_change_password) App.openChangePassword(true);
      } catch (err) {
        showErr(err.message);
        btn.disabled = false; btn.textContent = isReg ? "注册并进入" : "登录";
      }
    });
  },

  /* ----- 密码重置（凭管理员一次性链接，V1.3-B4.7） ----- */
  renderResetPassword(token) {
    const root = document.getElementById("app");
    root.innerHTML = "";
    const card = h(`
      <div class="auth-screen" style="grid-template-columns:1fr">
        <div class="auth-side" style="margin:auto"><div class="auth-card">
          <div class="auth-head"><h2>重置密码</h2><p>请设置新的登录密码（至少 8 位）</p></div>
          <div class="auth-error" style="display:none"></div>
          <form class="auth-form">
            <div class="field"><label>新密码</label><input name="p1" type="password" autocomplete="new-password" required></div>
            <div class="field"><label>确认新密码</label><input name="p2" type="password" autocomplete="new-password" required></div>
            <button type="submit" class="btn btn-primary auth-submit">设置并登录</button>
          </form>
          <div class="auth-switch"><a id="to-login">返回登录</a></div>
        </div></div>
      </div>`);
    root.appendChild(card);
    const errBox = card.querySelector(".auth-error");
    card.querySelector("#to-login").addEventListener("click", () => { history.replaceState(null, "", "/app"); App.renderAuth("login"); });
    card.querySelector(".auth-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(e.target);
      if (fd.get("p1") !== fd.get("p2")) { errBox.textContent = "两次输入不一致"; errBox.style.display = "block"; return; }
      try {
        const r = await API.resetPassword(token, fd.get("p1"));
        API.setToken(r.token); App.state.user = r.user;
        history.replaceState(null, "", "/app");
        await App.renderApp();
      } catch (err) { errBox.textContent = err.message; errBox.style.display = "block"; }
    });
  },

  /* ----- 主布局 ----- */
  async renderApp() {
    const u = App.state.user;
    const cfg = App.state.config;
    const root = document.getElementById("app");
    root.innerHTML = "";
    const layout = h(`
      <div class="layout">
        <aside class="sidebar">
          <div class="sb-head">
            <a href="/" title="返回首页"><img class="sb-univ" src="/assets/img/logo.svg" alt="华东政法大学"></a>
            <div class="sb-brand">
              <div class="sb-emblem">${Icons.logo}</div>
              <div><div class="sb-brand-name">${esc(cfg.platform_name)}</div><div class="sb-brand-sub">华东政法大学 · 涉外法治实验室</div></div>
            </div>
            <button class="btn btn-primary new-chat-btn" id="new-chat">${Icons.plus} 新建查明会话</button>
            ${u.role === "admin" ? `<button class="btn admin-entry" id="toggle-admin">${Icons.gauge} 管理控制台</button>` : ""}
          </div>
          <div class="sb-section-label">历史会话</div>
          <div class="conv-list" id="conv-list"></div>
          <div class="sb-foot">
            <div class="user-chip" id="user-chip">
              <div class="user-avatar">${esc((u.display_name || u.username)[0].toUpperCase())}</div>
              <div class="user-meta"><div class="user-name">${esc(u.display_name || u.username)}</div><div class="user-role">${ROLE_LABEL[u.role] || u.role}</div></div>
            </div>
          </div>
        </aside>
        <main class="main" id="main-view"></main>
        <button class="hamburger" id="hamburger" title="菜单">☰</button>
        <div class="sb-backdrop" id="sb-backdrop"></div>
      </div>`);
    root.appendChild(layout);

    // V1.3-B4.8 移动端：汉堡按钮开合侧栏
    const sb = layout.querySelector(".sidebar");
    const backdrop = layout.querySelector("#sb-backdrop");
    const closeSb = () => { sb.classList.remove("open"); backdrop.classList.remove("show"); };
    layout.querySelector("#hamburger").addEventListener("click", () => {
      sb.classList.toggle("open"); backdrop.classList.toggle("show", sb.classList.contains("open"));
    });
    backdrop.addEventListener("click", closeSb);
    App._closeSidebar = closeSb;

    layout.querySelector("#new-chat").addEventListener("click", () => App.newConversation());
    const adminBtn = layout.querySelector("#toggle-admin");
    if (adminBtn) adminBtn.addEventListener("click", () => App.switchView(App.state.view === "admin" ? "chat" : "admin"));
    layout.querySelector("#user-chip").addEventListener("click", () => App.openUserMenu());

    await App.refreshConversations();
    App.switchView("chat");
  },

  switchView(view) {
    App.state.view = view;
    const main = document.getElementById("main-view");
    const adminBtn = document.getElementById("toggle-admin");
    if (adminBtn) adminBtn.innerHTML = view === "admin" ? `${Icons.chat} 返回对话` : `${Icons.gauge} 管理控制台`;
    if (view === "admin") {
      Admin.mount(main);
    } else {
      Chat.mount(main);
      if (App.state.currentConvId) Chat.load(App.state.currentConvId);
      else Chat.showWelcome();
    }
  },

  async refreshConversations() {
    try {
      const r = await API.listConversations();
      App.state.conversations = r.conversations;
    } catch (e) { App.state.conversations = []; }
    App.renderConvList();
  },

  renderConvList() {
    const box = document.getElementById("conv-list");
    if (!box) return;
    const list = App.state.conversations;
    if (!list.length) { box.innerHTML = `<div class="conv-empty">还没有会话<br>点击上方开始查明</div>`; return; }
    box.innerHTML = "";
    list.forEach((c) => {
      const item = h(`<div class="conv-item ${c.id === App.state.currentConvId ? "active" : ""}">
          ${Icons.chat}<span class="conv-title">${esc(c.title || "新会话")}</span><span class="conv-ren" title="重命名">✎</span><span class="conv-del" title="删除">${Icons.trash}</span>
        </div>`);
      item.addEventListener("click", (e) => {
        if (e.target.closest(".conv-del")) { App.deleteConversation(c.id); return; }
        if (e.target.closest(".conv-ren")) { App.renameConversation(c); return; }
        App.openConversation(c.id);
        if (App._closeSidebar) App._closeSidebar();
      });
      box.appendChild(item);
    });
  },

  async renameConversation(c) {
    const title = prompt("会话重命名：", c.title || "");
    if (title === null) return;
    const t = (title || "").trim();
    if (!t) { flash("标题不能为空", true); return; }
    try {
      await API.renameConversation(c.id, t.slice(0, 200));
      await App.refreshConversations();
      flash("已重命名");
    } catch (e) { flash(e.message, true); }
  },

  openConversation(id) {
    App.state.currentConvId = id;
    App.state.view = "chat";
    App.renderConvList();
    Chat.mount(document.getElementById("main-view"));
    Chat.load(id);
  },

  newConversation() {
    App.state.currentConvId = null;
    App.state.view = "chat";
    App.renderConvList();
    Chat.mount(document.getElementById("main-view"));
    Chat.showWelcome();
  },

  async deleteConversation(id) {
    if (!confirm("确定删除该会话及其全部消息？")) return;
    try {
      await API.deleteConversation(id);
      if (App.state.currentConvId === id) App.state.currentConvId = null;
      await App.refreshConversations();
      if (!App.state.currentConvId) App.switchView("chat");
      flash("会话已删除");
    } catch (e) { flash(e.message, true); }
  },

  openUserMenu() {
    const u = App.state.user;
    const { close } = openModal(`
      <h3>账号</h3>
      <div class="kv" style="margin-bottom:18px">
        <span class="k">用户名</span><span>${esc(u.username)}</span>
        <span class="k">姓名</span><span>${esc(u.display_name || "—")}</span>
        <span class="k">角色</span><span><span class="role-tag">${ROLE_LABEL[u.role] || u.role}</span></span>
      </div>
      <button class="btn" id="m-chpwd" style="width:100%;margin-bottom:8px">修改密码</button>
      <button class="btn btn-danger" id="m-logout" style="width:100%">退出登录</button>`);
    document.getElementById("m-chpwd").addEventListener("click", () => { close(); App.openChangePassword(false); });
    document.getElementById("m-logout").addEventListener("click", () => App.logout());
  },

  openChangePassword(forced) {
    const { root, close } = openModal(`
      <h3>${forced ? "首次登录请修改密码" : "修改密码"}</h3>
      ${forced ? `<div class="auth-error" style="display:block;background:#fbf0d9;color:#b4791f;border-color:#f0e0b8">为保障安全，请先修改初始密码。</div>` : ""}
      <div class="auth-error" id="cp-err" style="display:none"></div>
      <div class="field"><label>原密码</label><input id="cp-old" type="password"></div>
      <div class="field"><label>新密码（至少 8 位）</label><input id="cp-new" type="password"></div>
      <div class="field"><label>确认新密码</label><input id="cp-new2" type="password"></div>
      <div class="modal-foot">${forced ? "" : '<button class="btn" id="cp-cancel">取消</button>'}<button class="btn btn-primary" id="cp-ok">确认修改</button></div>`);
    const err = (m) => { const e = root.querySelector("#cp-err"); e.textContent = m; e.style.display = "block"; };
    const cancel = root.querySelector("#cp-cancel");
    if (cancel) cancel.addEventListener("click", close);
    root.querySelector("#cp-ok").addEventListener("click", async () => {
      const o = root.querySelector("#cp-old").value, n = root.querySelector("#cp-new").value, n2 = root.querySelector("#cp-new2").value;
      if (n.length < 8) return err("新密码至少 8 位");
      if (n !== n2) return err("两次输入的新密码不一致");
      try {
        const r = await API.changePassword(o, n);
        // 改密会吊销旧 token；后端返回新 token，存下以保持当前端在线
        if (r && r.token) API.setToken(r.token);
        App.state.user.must_change_password = false;
        close();
        flash("密码已修改");
      } catch (e) { err(e.message); }
    });
  },

  logout() { API.setToken(null); location.href = "/"; },
};

window.addEventListener("DOMContentLoaded", App.boot);
