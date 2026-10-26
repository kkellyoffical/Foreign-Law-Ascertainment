/* SVG 图标库 + 可视化工具（计数动画 / 环形图 / 条形图）—— 企业级视觉基础设施 */
const Icons = (function () {
  const w = (paths, vb = "0 0 24 24", fill = false) =>
    `<svg class="ico" viewBox="${vb}" ${fill ? 'fill="currentColor" stroke="none"' : 'fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"'} aria-hidden="true">${paths}</svg>`;

  return {
    /* 品牌徽标：天平 + 盾形底座（双色，用于 logo） */
    logo: `<svg class="ico ico-logo" viewBox="0 0 48 48" fill="none" aria-hidden="true">
      <rect x="2" y="2" width="44" height="44" rx="12" fill="url(#zaLogoG)"/>
      <defs><linearGradient id="zaLogoG" x1="0" y1="0" x2="48" y2="48">
        <stop offset="0" stop-color="#b5232a"/><stop offset="1" stop-color="#d94f56"/>
      </linearGradient></defs>
      <g stroke="#fff" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round">
        <path d="M24 11v22"/><path d="M15 14.5h18"/><path d="M24 9.5a1.8 1.8 0 1 0 0.01 0z" fill="#fff"/>
        <path d="M15 14.5l-4.6 9.2h9.2L15 14.5z"/><path d="M10.4 23.7a4.6 4.6 0 0 0 9.2 0"/>
        <path d="M33 14.5l-4.6 9.2h9.2L33 14.5z"/><path d="M28.4 23.7a4.6 4.6 0 0 0 9.2 0"/>
        <path d="M18.5 35.5h11"/>
      </g></svg>`,
    scale: w('<path d="M12 4v16"/><path d="M5 7h14"/><path d="M5 7l-3.2 6.4h6.4L5 7z"/><path d="M1.8 13.4a3.2 3.2 0 0 0 6.4 0"/><path d="M19 7l-3.2 6.4h6.4L19 7z"/><path d="M15.8 13.4a3.2 3.2 0 0 0 6.4 0"/><path d="M8 21h8"/>'),
    plus: w('<path d="M12 5v14M5 12h14"/>'),
    chat: w('<path d="M21 12a8 8 0 0 1-8 8H4l2.2-2.6A8 8 0 1 1 21 12z"/>'),
    trash: w('<path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/>'),
    shield: w('<path d="M12 3l8 3.5v5.2c0 4.6-3.2 7.6-8 9.3-4.8-1.7-8-4.7-8-9.3V6.5L12 3z"/><path d="M9 12l2.2 2.2L15.5 10"/>'),
    gauge: w('<path d="M4 14a8 8 0 1 1 16 0"/><path d="M12 14l4.2-4.2"/><circle cx="12" cy="14" r="1.6"/><path d="M3 19h18"/>'),
    send: w('<path d="M12 19V5"/><path d="M6 11l6-6 6 6"/>'),
    search: w('<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/>'),
    book: w('<path d="M4 19.5V5a2 2 0 0 1 2-2h14v16H6a2 2 0 0 0-2 2.5z"/><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20v4H6.5A2.5 2.5 0 0 1 4 19.5z"/><path d="M9 7h7M9 10.5h5"/>'),
    gavel: w('<path d="M13.5 5.5l5 5"/><path d="M11 8l5 5"/><path d="M14.5 4.5l4 4a1.4 1.4 0 0 1-2 2l-4-4a1.4 1.4 0 0 1 2-2z"/><path d="M9.5 9.5l-6 6a1.4 1.4 0 0 0 2 2l6-6"/><path d="M12 21H3"/>'),
    doc: w('<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8l-5-5z"/><path d="M14 3v5h5"/><path d="M9 13h6M9 16.5h6"/>'),
    megaphone: w('<path d="M3 11v3a1 1 0 0 0 1 1h2l3 5h2v-5"/><path d="M11 15l8 3V6l-8 3H4a1 1 0 0 0-1 1v1"/><path d="M21.5 10.5v3"/>'),
    compass: w('<circle cx="12" cy="12" r="9"/><path d="M15.5 8.5l-2.2 5-5 2.2 2.2-5 5-2.2z"/>'),
    globe: w('<circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3a14.5 14.5 0 0 1 0 18a14.5 14.5 0 0 1 0-18z"/>'),
    users: w('<circle cx="9" cy="8" r="3.4"/><path d="M2.8 20a6.2 6.2 0 0 1 12.4 0"/><path d="M16 5.2a3.4 3.4 0 0 1 0 5.9"/><path d="M17.6 14.4a6.2 6.2 0 0 1 3.6 5.6"/>'),
    key: w('<circle cx="8" cy="14" r="4.2"/><path d="M11 11l9-9"/><path d="M17 5l2.5 2.5M14.5 7.5L17 10"/>'),
    chart: w('<path d="M3 3v18h18"/><path d="M8 16v-5M13 16V8M18 16v-8"/>'),
    layers: w('<path d="M12 3l9 5-9 5-9-5 9-5z"/><path d="M3 13l9 5 9-5"/><path d="M3 17.5l9 5 9-5" opacity=".45"/>'),
    database: w('<ellipse cx="12" cy="5.5" rx="8" ry="3"/><path d="M4 5.5V12c0 1.7 3.6 3 8 3s8-1.3 8-3V5.5"/><path d="M4 12v6.5c0 1.7 3.6 3 8 3s8-1.3 8-3V12"/>'),
    copy: w('<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>'),
    external: w('<path d="M14 4h6v6"/><path d="M20 4l-9 9"/><path d="M19 13.5V19a1.5 1.5 0 0 1-1.5 1.5h-12A1.5 1.5 0 0 1 4 19V6.5A1.5 1.5 0 0 1 5.5 5H11"/>'),
    check: w('<path d="M4.5 12.5l5 5L19.5 7"/>'),
    x: w('<path d="M6 6l12 12M18 6L6 18"/>'),
    warning: w('<path d="M12 3.5L22 20H2L12 3.5z"/><path d="M12 10v4.5"/><circle cx="12" cy="17.2" r=".4" fill="currentColor"/>'),
    lock: w('<rect x="5" y="11" width="14" height="9.5" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/><circle cx="12" cy="15.6" r="1.3"/>'),
    user: w('<circle cx="12" cy="8" r="3.8"/><path d="M4.5 20.5a7.5 7.5 0 0 1 15 0"/>'),
    logout: w('<path d="M9 21H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3"/><path d="M16 17l5-5-5-5"/><path d="M21 12H10"/>'),
    sparkle: w('<path d="M12 3l1.9 5.6L19.5 10.5l-5.6 1.9L12 18l-1.9-5.6L4.5 10.5l5.6-1.9L12 3z"/><path d="M19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8L19 16z"/>'),
    arrowLeft: w('<path d="M19 12H5"/><path d="M11 18l-6-6 6-6"/>'),
    arrowRight: w('<path d="M5 12h14"/><path d="M13 6l6 6-6 6"/>'),
    clock: w('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.2 1.8"/>'),
    settings: w('<circle cx="12" cy="12" r="3.2"/><path d="M19.4 13.5a1.8 1.8 0 0 0 .4 2l.1.1a2 2 0 1 1-2.9 2.9l-.1-.1a1.8 1.8 0 0 0-2-.4 1.8 1.8 0 0 0-1.1 1.7v.2a2 2 0 1 1-4.1 0v-.2a1.8 1.8 0 0 0-1.2-1.7 1.8 1.8 0 0 0-2 .4l-.1.1a2 2 0 1 1-2.9-2.9l.1-.1a1.8 1.8 0 0 0 .4-2 1.8 1.8 0 0 0-1.7-1.1h-.2a2 2 0 1 1 0-4.1h.2a1.8 1.8 0 0 0 1.7-1.2 1.8 1.8 0 0 0-.4-2l-.1-.1a2 2 0 1 1 2.9-2.9l.1.1a1.8 1.8 0 0 0 2 .4h.1a1.8 1.8 0 0 0 1.1-1.7v-.2a2 2 0 1 1 4.1 0v.2a1.8 1.8 0 0 0 1.1 1.7h.1a1.8 1.8 0 0 0 2-.4l.1-.1a2 2 0 1 1 2.9 2.9l-.1.1a1.8 1.8 0 0 0-.4 2v.1a1.8 1.8 0 0 0 1.7 1.1h.2a2 2 0 1 1 0 4.1h-.2a1.8 1.8 0 0 0-1.7 1.1z"/>'),
    history: w('<path d="M3.5 12a8.5 8.5 0 1 1 2.5 6"/><path d="M3.5 18v-6h6" transform="translate(0 -6) scale(1)" opacity="0"/><path d="M3 12h4M3 12V8"/><path d="M12 7.5V12l3 1.8"/>'),
    grid: w('<rect x="3.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="3.5" y="13.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="13.5" width="7" height="7" rx="1.5"/>'),
  };
})();

/* ---------- 数字滚动动画 ---------- */
function countUp(el, target, duration = 1100) {
  const t = Number(target) || 0;
  const start = performance.now();
  const ease = (x) => 1 - Math.pow(1 - x, 3);
  const step = (now) => {
    const p = Math.min((now - start) / duration, 1);
    el.textContent = Math.round(t * ease(p)).toLocaleString("en-US");
    if (p < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

/* ---------- SVG 环形图 ----------
   segments: [{label, value, color}]，返回 {html, animate()}。
   animate() 在节点插入 DOM 后调用，触发描边生长动画。 */
function donutChart(segments, { size = 168, stroke = 22, centerTitle = "", centerSub = "" } = {}) {
  const total = segments.reduce((s, x) => s + x.value, 0) || 1;
  const r = (size - stroke) / 2;
  const C = 2 * Math.PI * r;
  let offset = 0;
  const id = "dn" + Math.random().toString(36).slice(2, 8);
  const circles = segments.map((s, i) => {
    const frac = s.value / total;
    const seg = `<circle class="donut-seg" data-donut="${id}" data-dash="${frac * C}"
      cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none"
      stroke="${s.color}" stroke-width="${stroke}" stroke-linecap="butt"
      stroke-dasharray="0 ${C}" stroke-dashoffset="${-offset}"
      style="transition: stroke-dasharray 1s cubic-bezier(.22,.8,.36,1) ${i * 0.12}s"/>`;
    offset += frac * C;
    return seg;
  }).join("");
  const html = `
    <svg class="donut" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
      <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="var(--bg-3)" stroke-width="${stroke}"/>
      <g transform="rotate(-90 ${size / 2} ${size / 2})">${circles}</g>
      <text x="50%" y="47%" text-anchor="middle" class="donut-title">${centerTitle}</text>
      <text x="50%" y="60%" text-anchor="middle" class="donut-sub">${centerSub}</text>
    </svg>`;
  const animate = () => {
    document.querySelectorAll(`[data-donut="${id}"]`).forEach((c) => {
      requestAnimationFrame(() =>
        requestAnimationFrame(() => {
          const d = parseFloat(c.dataset.dash);
          c.setAttribute("stroke-dasharray", `${d} ${C - d}`);
        })
      );
    });
  };
  return { html, animate };
}

/* ---------- 动画条形分布 ----------
   rows: [{k,n}]，返回 html；插入后调用 animateBars(container) 触发宽度生长。 */
function barList(rows, labelMap, palette) {
  const max = Math.max(...rows.map((r) => r.n), 1);
  const get = (m, k) => (m && m[k]) || k || "（未分类）";
  return rows.map((r, i) => `
    <div class="bar-row" style="animation-delay:${i * 0.04}s">
      <div class="bar-label" title="${esc(get(labelMap, r.k))}">${esc(get(labelMap, r.k))}</div>
      <div class="bar-track"><div class="bar-fill" data-w="${(r.n / max) * 100}"
        style="background:${palette ? palette[i % palette.length] : "var(--brand)"}"></div></div>
      <div class="bar-val">${Number(r.n).toLocaleString("en-US")}</div>
    </div>`).join("");
}
function animateBars(container) {
  container.querySelectorAll(".bar-fill[data-w]").forEach((b) =>
    requestAnimationFrame(() => requestAnimationFrame(() => { b.style.width = b.dataset.w + "%"; }))
  );
}
