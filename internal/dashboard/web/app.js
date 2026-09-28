const q = (selector, root = document) => root.querySelector(selector);
const qa = (selector, root = document) => [...root.querySelectorAll(selector)];

const text = (selector, value) => {
  const element = q(selector);
  if (element) element.textContent = value;
};

const formatNumber = value => new Intl.NumberFormat('ja-JP').format(value ?? 0);
const formatAge = seconds => seconds < 1 ? '<1秒前' : `${Math.round(seconds)}秒前`;
const formatBytes = bytes => {
  if (!bytes) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  const unit = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** unit).toFixed(unit ? 1 : 0)} ${units[unit]}`;
};
const escapeHTML = value => String(value ?? '').replace(/[&<>'"]/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[character]));

function renderStatus(state) {
  const overall = state.overall || 'starting';
  const pill = q('#overall-pill');
  pill.className = `status-pill ${overall}`;
  pill.innerHTML = `<span class="status-dot"></span>${escapeHTML(overall.toUpperCase())}`;
  const updated = new Date(state.updated_at);
  text('#updated-at', Number.isNaN(updated.getTime()) ? '更新待ち' : `${updated.toLocaleTimeString('ja-JP')} · ${state.uplane_probe?.interval_seconds || 5}秒収集`);

  const controller = state.controller || {};
  text('#lease-value', controller.observer_lease_valid ? 'VALID' : 'EXPIRED');
  text('#lease-age', controller.observer_last_seen ? formatAge(controller.observer_age_seconds || 0) : '未受信');
  q('#lease-rule').style.width = controller.observer_lease_valid ? '100%' : '12%';
  q('#lease-rule').style.backgroundColor = controller.observer_lease_valid ? 'var(--green)' : 'var(--red)';

  const bgp = String(controller.bgp_state || '0/0 unknown');
  const counts = bgp.match(/(\d+)\/(\d+)/);
  text('#bgp-value', counts ? `${counts[1]} / ${counts[2]}` : '—');
  text('#bgp-state', bgp.includes('established') ? 'Established' : bgp);
  q('#bgp-rule').style.width = counts && Number(counts[2]) ? `${Number(counts[1]) / Number(counts[2]) * 100}%` : '0';

  text('#session-value', `${controller.selected_sessions ?? 0} / ${controller.observed_sessions ?? 0}`);
  const sessionRatio = controller.observed_sessions ? controller.selected_sessions / controller.observed_sessions * 100 : 0;
  q('#session-rule').style.width = `${sessionRatio}%`;
  text('#route-value', formatNumber(controller.advertised_routes));
  text('#path-mode', state.stale ? 'Unknown · stale snapshot' : state.paths?.mup_active ? 'MUP bypass active' : 'UPF fallback');
  text('#collection-duration', `${state.collection_duration_ms ?? 0} ms`);
}

function renderTopology(state) {
  const nodes = new Map((state.nodes || []).map(node => [node.id, node]));
  qa('[data-node]').forEach(group => {
    const node = nodes.get(group.dataset.node);
    const status = node?.healthy ? 'healthy' : node?.reachable ? 'degraded' : 'offline';
    group.classList.remove('healthy', 'degraded', 'offline');
    group.classList.add(status);
  });
  const topology = q('#topology');
  topology.classList.toggle('fallback-active', !state.stale && !state.paths?.mup_active);
  topology.classList.remove('probe-ok', 'probe-failed', 'probe-idle');
  const probe = state.uplane_probe || {status: 'idle'};
  const probeStatus = ['ok', 'failed'].includes(probe.status) ? probe.status : 'idle';
  topology.classList.add(`probe-${probeStatus}`);
  const pathName = state.paths?.mup_active ? 'MUP' : 'UPF';
  if (probe.success) {
    text('#active-flow', `${pathName} · ICMP ${Number(probe.rtt_ms).toFixed(2)} ms`);
  } else if (probeStatus === 'failed') {
    text('#active-flow', `${pathName} · ICMP FAILED`);
  } else {
    text('#active-flow', 'U-PLANE PROBE WAITING');
  }

  const probeElement = q('#uplane-probe');
  probeElement.className = `uplane-status ${probeStatus}`;
  if (probe.success) {
    probeElement.innerHTML = `<i></i>ICMP ${escapeHTML(probe.source)} → ${escapeHTML(probe.target)} · ${Number(probe.rtt_ms).toFixed(2)} ms`;
  } else if (probeStatus === 'failed') {
    probeElement.innerHTML = `<i></i>ICMP ${escapeHTML(probe.source || 'UE')} → ${escapeHTML(probe.target || 'DN')} · FAILED`;
  } else {
    probeElement.innerHTML = '<i></i>U-Plane probe waiting';
  }

  const ue = state.sessions?.[0]?.ue_ipv4 || probe.source;
  text('#mupc-address', nodes.get('mupc')?.address || 'management address');
  text('#core-address', nodes.get('core')?.address ? `N4 observer · ${nodes.get('core').address}` : 'N4 observer');
  text('#ran-address', ue ? `UE ${ue}` : nodes.get('ran')?.address || 'UERANSIM');
  text('#dn-address', probe.target || nodes.get('dn')?.address || 'data target');
}

function renderSessions(sessions = []) {
  text('#session-count', sessions.length);
  const container = q('#sessions');
  if (!sessions.length) {
    container.innerHTML = '<div class="empty">PFCPセッションはありません</div>';
    return;
  }
  container.innerHTML = sessions.map(session => `
    <article class="session-card ${session.advertised ? 'active' : ''}">
      <div class="session-title">
        <strong class="session-ue">${escapeHTML(session.ue_ipv4)}</strong>
        <span class="session-state ${session.advertised ? '' : 'inactive'}">${session.advertised ? 'T1 + T2 ADVERTISED' : session.suppressed ? 'SUPPRESSED' : 'FALLBACK'}</span>
      </div>
      <div class="session-grid">
        <div class="field"><label>DNN</label><span>${escapeHTML(session.dnn)}</span></div>
        <div class="field"><label>QFI</label><span>${formatNumber(session.qfi)}</span></div>
        <div class="field"><label>UPLINK F-TEID</label><span>${escapeHTML(session.upf_gtp_ipv4)}:${formatNumber(session.uplink_teid)}</span></div>
        <div class="field"><label>DOWNLINK F-TEID</label><span>${escapeHTML(session.ran_gtp_ipv4)}:${formatNumber(session.downlink_teid)}</span></div>
        <div class="field"><label>CP / UP SEID</label><span>${formatNumber(session.cp_seid)} / ${formatNumber(session.up_seid)}</span></div>
        <div class="field"><label>POLICY</label><span>${escapeHTML(session.reason || '—')}</span></div>
      </div>
      <div class="session-key">${escapeHTML(session.key)}</div>
    </article>`).join('');
}

function statByName(stats, name) {
  return (stats || []).find(stat => stat.name === name) || {packets: 0, bytes: 0};
}

// Keep stable API/Compose IDs separate from the Japanese presentation labels.
const peLabels = {tpe: 'MUP PE（N3／Interwork側）', npe: 'MUP PE（N6／Direct側）'};

function renderPEs(pes = []) {
  const container = q('#pe-stats');
  if (!pes.length) {
    container.innerHTML = '<div class="empty">PE統計を取得中</div>';
    return;
  }
  container.innerHTML = pes.map(pe => {
    const rx = statByName(pe.stats, 'RX_PACKETS');
    const pass = statByName(pe.stats, 'PASS');
    const redirect = statByName(pe.stats, 'REDIRECT');
    const drop = statByName(pe.stats, 'DROP');
    const denominator = Math.max(rx.packets || 0, 1);
    const route = pe.routes?.[0];
    const headend = pe.headends?.[0];
    return `<article class="pe-card">
      <div class="pe-title"><strong>${escapeHTML(peLabels[pe.id] || pe.name)}</strong><span class="route-chip">${escapeHTML((route?.route_type || '—').toUpperCase())}</span></div>
      <div class="pe-identity">ID: ${escapeHTML(pe.id)}</div>
      ${[
        ['RX', rx], ['REDIRECT', redirect], ['PASS', pass], ['DROP', drop]
      ].map(([label, stat]) => `<div class="stat-row"><label>${label}</label><div class="stat-bar"><i style="width:${Math.min((stat.packets || 0) / denominator * 100, 100)}%"></i></div><span>${formatNumber(stat.packets)} · ${formatBytes(stat.bytes)}</span></div>`).join('')}
      <div class="pe-route"><b>SID</b> ${escapeHTML(route?.srv6_sid || '—')}<br><b>HEADEND</b> ${escapeHTML(headend?.trigger_prefix || '—')} → ${escapeHTML(headend?.segments?.[0] || headend?.dst_addr || '—')}</div>
    </article>`;
  }).join('');
}

function renderNodes(nodes = []) {
  const healthy = nodes.filter(node => node.healthy).length;
  text('#healthy-node-count', `${healthy} / ${nodes.length} healthy`);
  q('#node-list').innerHTML = nodes.map(node => {
    const status = node.healthy ? 'healthy' : node.reachable ? 'degraded' : 'offline';
    const services = (node.services || []).map(service => `${service.name}:${service.state}`).join(' · ');
    const label = peLabels[node.id] || node.name;
    const identity = peLabels[node.id] ? `<small class="node-identity">${escapeHTML(node.name)} · ID: ${escapeHTML(node.id)}</small>` : '';
    return `<div class="node-row"><i class="health-dot ${status}"></i><strong>${escapeHTML(label)}${identity}</strong><span class="services" title="${escapeHTML(services)}">${escapeHTML(services)}</span></div>`;
  }).join('');
}

function renderDiagnostics(errors = []) {
  const container = q('#diagnostics');
  container.innerHTML = errors.length
    ? errors.map(error => `<div class="error-item">${escapeHTML(error)}</div>`).join('')
    : '<div class="ok-message"><span>✓</span>異常は検出されていません</div>';
}

function render(state) {
  renderStatus(state);
  renderTopology(state);
  renderSessions(state.sessions || []);
  renderPEs(state.pes || []);
  renderNodes(state.nodes || []);
  renderDiagnostics(state.errors || []);
}

async function refresh() {
  try {
    const response = await fetch('/api/state', {cache: 'no-store'});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    render(await response.json());
  } catch (error) {
    renderTopology({stale: true, paths: {}, uplane_probe: {status: 'idle'}});
    const pill = q('#overall-pill');
    pill.className = 'status-pill offline';
    pill.innerHTML = '<span class="status-dot"></span>DISCONNECTED';
    renderDiagnostics([`ダッシュボードAPI: ${error.message}`]);
  }
}

refresh();
setInterval(refresh, 5000);
