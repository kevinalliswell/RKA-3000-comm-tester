<script lang="ts">
  import { onMount } from 'svelte';
  import { ScanSession } from './lib/session.svelte';
  import type { DeviceType, ScanResult } from './lib/api';

  const session = new ScanSession();
  const deviceNames: Record<DeviceType, string> = {
    taie: 'TAIE 温控仪', azbil: 'Azbil MFC', balance: '电子天平',
  };
  const statuses: Record<string, string> = {
    queued: '等待扫描', running: '扫描中', completed: '已完成', cancelled: '已停止',
    online: '在线', offline: '无响应', degraded: '响应不完整', error: '错误',
  };
  const tareStatuses: Record<string, string> = { queued: '等待去皮', running: '去皮测试中' };
  const isBalance = $derived(session.config.device === 'balance');
  const jobStatus = $derived(session.job
    ? (session.job.operation === 'tare' && tareStatuses[session.job.status]) || statuses[session.job.status]
    : '待机');
  const maxAddress = $derived(session.config.device === 'azbil' ? 127 : 247);
  const onlineCount = $derived(session.job?.results.filter((result) => result.status === 'online').length ?? 0);
  let importInput: HTMLInputElement;

  onMount(() => {
    void session.initialize();
    return () => session.dispose();
  });

  function confirmTare() {
    const message = '去皮会把天平当前读数清零（改变天平零点）。\n请确认秤盘为空或只放置需要扣除的容器。\n\n继续发送去皮命令？';
    if (window.confirm(message)) void session.beginTare();
  }

  function valueSummary(result: ScanResult) {
    return Object.entries(result.values).map(([key, value]) =>
      `${key}: ${typeof value === 'object' ? JSON.stringify(value) : value}`).join(' · ');
  }
</script>

<svelte:head><title>RKA-3000 通信测试</title></svelte:head>

<main class="shell">
  <header class="topbar">
    <div class="brand">
      <div class="brand-mark" aria-hidden="true">R3</div>
      <div><div class="eyebrow">RKA-3000 / FIELD DIAGNOSTICS</div><h1>通信测试台</h1></div>
    </div>
    <div class:connected={session.connected} class="service-pill" role="status">
      <span class="pulse"></span>{session.connected ? '本地服务在线' : '等待本地服务'}
    </div>
  </header>

  <section class="workspace">
    <aside class="control-panel">
      <div class="panel-heading"><div><div class="eyebrow">SCAN PROFILE</div><h2>扫描参数</h2></div><span class="layer-tag">{isBalance ? '诊断 · 去皮需确认' : '只读诊断'}</span></div>
      <form onsubmit={(event) => { event.preventDefault(); void session.beginScan(); }}>
        <fieldset disabled={session.active}>
          <label class="field"><span>设备协议</span>
            <select value={session.config.device} onchange={(event) => session.selectDevice(event.currentTarget.value as DeviceType)}>
              <option value="taie">TAIE 温控仪</option><option value="azbil">Azbil MFC</option><option value="balance">电子天平</option>
            </select>
          </label>
          <label class="field port-field"><span>串口</span>
            <input bind:value={session.config.port} list="serial-ports" placeholder="选择或输入 COM 口" required />
            <datalist id="serial-ports">{#each session.ports as port}<option value={port.device}>{port.description}</option>{/each}</datalist>
          </label>
          <div class="field-grid two">
            <label class="field"><span>波特率</span><input type="number" bind:value={session.config.baudrate} min="300" max="921600" step="1" required /></label>
            <label class="field"><span>超时 / 秒</span><input type="number" bind:value={session.config.timeout_s} min="0.05" max="10" step="0.05" required /></label>
            <label class="field"><span>校验位</span><select bind:value={session.config.parity}><option>N</option><option>E</option><option>O</option></select></label>
            <label class="field"><span>停止位</span><select bind:value={session.config.stopbits}><option value={1}>1</option><option value={2}>2</option></select></label>
          </div>
          {#if session.config.device === 'azbil'}<p class="hint">Azbil 支持 8E1 或 8N2，地址范围 1–127。</p>{/if}
          <div class:muted={isBalance} class="section-divider"><span>站号范围</span><span class="hint">{isBalance ? '天平不使用站号' : `1–${maxAddress}`}</span></div>
          <div class="field-grid two">
            <label class="field"><span>起始地址</span><input type="number" bind:value={session.config.start_address} min="1" max={maxAddress} required disabled={isBalance} /></label>
            <label class="field"><span>结束地址</span><input type="number" bind:value={session.config.end_address} min="1" max={maxAddress} required disabled={isBalance} /></label>
          </div>
          {#if isBalance}
            <div class="section-divider"><span>天平选项</span></div>
            <div class="field-grid two">
              <label class="field"><span>协议</span><select bind:value={session.config.balance_protocol}><option value="gs_cpa">GS-CPA</option><option value="longteng">龙腾</option></select></label>
              <label class="field"><span>读取帧数</span><input type="number" bind:value={session.config.balance_lines} min="1" max="100" step="1" required /></label>
            </div>
            <label class="toggle-row"><input type="checkbox" bind:checked={session.config.balance_start_output} disabled={session.config.balance_protocol !== 'gs_cpa'} /><span>发送 O1 启动连续输出（结束时发送 O0）</span></label>
            <div class="section-divider"><span>去皮测试</span><span class="hint">发送去皮命令并等待稳定零点</span></div>
            <div class="field-grid two">
              <label class="field"><span>等待 / 秒</span><input type="number" bind:value={session.config.tare_timeout_s} min="1" max="30" step="0.5" required /></label>
              <label class="field"><span>零点容差 / g</span><input type="number" bind:value={session.config.tare_zero_tolerance_g} min="0.001" max="10" step="0.001" required /></label>
            </div>
          {/if}
        </fieldset>
        <div class="actions">
          <button type="submit" class="button primary" disabled={!session.connected || session.active}>开始扫描</button>
          <button type="button" class="button secondary" onclick={() => session.stopScan()} disabled={!session.active || session.submitting || session.cancelling}>{session.cancelling ? '停止中…' : '停止'}</button>
          <button type="button" class="button quiet" onclick={() => session.refreshPorts()} disabled={!session.connected || session.active}>刷新串口</button>
          {#if isBalance}<button type="button" class="button tare" onclick={confirmTare} disabled={!session.connected || session.active}>去皮测试</button>{/if}
        </div>
      </form>
      <div class="auto-row"><label><input type="checkbox" checked={session.autoReview} onchange={(event) => session.cancelReview(event.currentTarget.checked)} />自动复查</label><input aria-label="复查间隔（秒）" class="interval" type="number" bind:value={session.autoInterval} min="5" max="300" disabled={!session.autoReview || session.active} /><span>秒</span></div>
      <div class="profile-actions">
        <button class="button quiet" onclick={() => session.save()} disabled={!session.connected || session.active}>保存配置</button>
        <button class="button quiet" onclick={() => importInput.click()} disabled={!session.connected || session.active}>导入配置</button>
        <button class="button quiet" onclick={() => session.downloadProfile()} disabled={!session.connected || session.active}>导出配置</button>
        <input class="file-input" type="file" accept=".json,application/json" aria-label="导入配置文件" bind:this={importInput} onchange={(event) => { const file = event.currentTarget.files?.[0]; if (file) void session.importProfile(file); event.currentTarget.value = ''; }} />
      </div>
    </aside>

    <section class="results-panel">
      <div class="results-heading"><div><div class="eyebrow">LIVE RESULT STREAM</div><h2>扫描结果</h2></div><div class="job-state"><span class:working={session.active} class="state-dot"></span>{jobStatus}</div></div>
      <div class="metric-row">
        <div class="metric"><span>当前任务</span><strong>{session.job ? session.job.id.slice(0, 8) : '—'}</strong></div>
        <div class="metric"><span>已返回</span><strong>{session.job?.results.length ?? 0}</strong></div>
        <div class="metric success"><span>在线设备</span><strong>{onlineCount}</strong></div>
        <div class="metric"><span>协议</span><strong>{deviceNames[session.job?.config.device ?? session.config.device]}</strong></div>
      </div>
      {#if session.error}<div class="notice error" role="alert"><strong>操作未完成</strong><span>{session.error}</span></div>{/if}
      <div class="notice service" role="status"><span class="notice-dot"></span>{session.message}</div>
      <div class="table-wrap">
        <table><thead><tr><th>时间</th><th>设备</th><th>串口</th><th>地址</th><th>状态</th><th>读取值</th><th>说明</th></tr></thead>
          <tbody>
            {#if session.job?.results.length}
              {#each session.job.results as result}
                <tr><td class="mono">{new Date(result.timestamp).toLocaleTimeString('zh-CN', { hour12: false })}</td><td>{deviceNames[result.device]}</td><td class="mono">{result.port}</td><td class="mono">{result.address ?? '—'}</td><td><span class={`status status-${result.status}`}>{statuses[result.status] ?? result.status}</span></td><td class="values">{valueSummary(result) || '—'}</td><td>{result.detail || '—'}</td></tr>
              {/each}
            {:else}<tr class="empty"><td colspan="7"><span class="empty-icon" aria-hidden="true">◎</span><strong>等待扫描结果</strong><span>配置串口和协议后开始一次只读探测，天平可另行执行去皮测试</span></td></tr>{/if}
          </tbody>
        </table>
      </div>
      <footer class="results-footer"><span>扫描不下发设定值或去皮命令；去皮仅在点击「去皮测试」并确认后发送；GS-CPA 连续输出需主动勾选。</span><button class="button quiet" onclick={() => session.downloadLog()} disabled={!session.connected}>导出日志</button></footer>
      <p class="log-path" title={session.logPath}>日志：{session.logPath || '等待本地服务连接'}</p>
    </section>
  </section>
</main>
