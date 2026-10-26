/* API 封装：统一鉴权、错误处理、SSE 流式 */
const API = (function () {
  const TOKEN_KEY = "za_token";
  const getToken = () => localStorage.getItem(TOKEN_KEY);
  const setToken = (t) => (t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY));

  async function req(method, path, body) {
    const headers = {};
    const token = getToken();
    if (token) headers["Authorization"] = "Bearer " + token;
    const opts = { method, headers };
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(path, opts);
    let data = null;
    try { data = await res.json(); } catch (e) {}
    if (!res.ok) {
      const detail = data && (data.detail || data.error);
      const err = new Error(typeof detail === "string" ? detail : "请求失败（HTTP " + res.status + "）");
      err.status = res.status;
      throw err;
    }
    return data;
  }

  function qstr(params) {
    const qs = new URLSearchParams();
    Object.entries(params || {}).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== "") qs.append(k, v);
    });
    return qs.toString();
  }

  return {
    getToken, setToken,
    isLoggedIn: () => !!getToken(),

    // 认证
    login: (username, password) => req("POST", "/api/auth/login", { username, password }),
    register: (payload) => req("POST", "/api/auth/register", payload),
    me: () => req("GET", "/api/auth/me"),
    changePassword: (old_password, new_password) =>
      req("POST", "/api/auth/change-password", { old_password, new_password }),
    resetPassword: (token, new_password) =>
      req("POST", "/api/auth/reset-password", { token, new_password }),
    publicConfig: () => req("GET", "/api/config"),

    // 会话
    listConversations: () => req("GET", "/api/conversations"),
    createConversation: (title) => req("POST", "/api/conversations", { title: title || null }),
    getConversation: (id) => req("GET", "/api/conversations/" + id),
    renameConversation: (id, title) => req("PATCH", "/api/conversations/" + id, { title }),
    deleteConversation: (id) => req("DELETE", "/api/conversations/" + id),

    // 流式问答：handlers(eventName, dataObj)；返回 Promise
    chatStream: async function (convId, message, handlers, signal, mode) {
      const res = await fetch("/api/conversations/" + convId + "/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: "Bearer " + getToken() },
        body: JSON.stringify({ message, mode: mode || "fast" }),  // 与后端 ChatReq 默认一致（V1.3 修地雷）
        signal,
      });
      if (!res.ok) {
        let d = {};
        try { d = await res.json(); } catch (e) {}
        throw new Error(d.detail || "对话请求失败（HTTP " + res.status + "）");
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        // sse-starlette 默认以 \r\n 分隔；剥离 \r 统一按 \n 处理
        buf += decoder.decode(value, { stream: true }).replace(/\r/g, "");
        let idx;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          const chunk = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          let event = "message";
          let dataStr = "";
          chunk.split("\n").forEach((line) => {
            if (line.startsWith("event:")) event = line.slice(6).trim();
            else if (line.startsWith("data:")) dataStr += line.slice(5).trim();
          });
          if (dataStr) {
            let data;
            try { data = JSON.parse(dataStr); } catch (e) { data = { raw: dataStr }; }
            handlers(event, data);
          }
        }
      }
    },

    // 管理
    adminStats: () => req("GET", "/api/admin/stats"),
    adminUsers: () => req("GET", "/api/admin/users"),
    adminCreateUser: (payload) => req("POST", "/api/admin/users", payload),
    adminUpdateUser: (id, payload) => req("PATCH", "/api/admin/users/" + id, payload),
    adminResetLink: (id) => req("POST", "/api/admin/users/" + id + "/reset-link"),
    adminAlerts: (limit) => req("GET", "/api/admin/alerts?" + qstr({ limit })),
    adminQuality: () => req("GET", "/api/admin/quality"),
    adminInvites: () => req("GET", "/api/admin/invitations"),
    adminCreateInvite: (payload) => req("POST", "/api/admin/invitations", payload),
    adminDeleteInvite: (id) => req("DELETE", "/api/admin/invitations/" + id),
    adminSearchCases: (params) => req("GET", "/api/admin/cases/search?" + qstr(params)),
    adminCaseDetail: (id) => req("GET", "/api/admin/cases/" + id),
    adminSearchStatutes: (params) => req("GET", "/api/admin/statutes/search?" + qstr(params)),

    // 案例清洗全文（登录用户可取）
    caseFulltext: (id) => req("GET", "/api/cases/" + id + "/fulltext"),

    // 导出查明意见书（pdf|docx）：带鉴权下载 → blob → 触发保存；meta 可带委托方/承办人/事项编号
    downloadReport: async function (convId, format, meta) {
      const q = qstr(Object.assign({ format: format || "pdf" }, meta || {}));
      const res = await fetch("/api/conversations/" + convId + "/report?" + q, {
        headers: { Authorization: "Bearer " + getToken() },
      });
      if (!res.ok) {
        let d = {};
        try { d = await res.json(); } catch (e) {}
        throw new Error(d.detail || "导出失败（HTTP " + res.status + "）");
      }
      const blob = await res.blob();
      // 从 Content-Disposition 的 filename* 取中文文件名
      let fname = "查明意见书." + (format || "pdf");
      const cd = res.headers.get("Content-Disposition") || "";
      const m = cd.match(/filename\*=UTF-8''([^;]+)/);
      if (m) { try { fname = decodeURIComponent(m[1]); } catch (e) {} }
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = fname;
      document.body.appendChild(a); a.click();
      setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 800);
    },
  };
})();
