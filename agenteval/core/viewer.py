"""自包含单文件 HTML Trace Viewer（PLAN 第 10 节）。

`cli view <run_id>` 生成：摘要卡片 + 按 split/结果/reason code 筛选 +
点击 case 下钻看消息流、工具调用树、每个 grader 的判定理由。
纯内联 JSON + 原生 JS，零 CDN 依赖，双击即可在浏览器打开。
"""

from __future__ import annotations

import json
from pathlib import Path

from agenteval.core.harness import load_run

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>AgentEval Trace Viewer — __RUN_ID__</title>
<style>
  :root { --ok:#16a34a; --bad:#dc2626; --muted:#6b7280; --line:#e5e7eb; }
  * { box-sizing: border-box; }
  body { font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
         margin: 0; background: #f8fafc; color: #111827; }
  header { background: #1e293b; color: #fff; padding: 16px 24px; }
  header h1 { font-size: 18px; margin: 0 0 4px; }
  header .meta { font-size: 12px; color: #94a3b8; }
  .banner { background: #fef3c7; color: #92400e; padding: 8px 24px; font-size: 13px; }
  .cards { display: flex; gap: 12px; padding: 16px 24px; flex-wrap: wrap; }
  .card { background: #fff; border: 1px solid var(--line); border-radius: 8px;
          padding: 12px 18px; min-width: 140px; }
  .card .v { font-size: 22px; font-weight: 700; }
  .card .k { font-size: 12px; color: var(--muted); }
  .filters { padding: 0 24px 12px; display: flex; gap: 8px; flex-wrap: wrap; }
  select, input { padding: 6px 10px; border: 1px solid var(--line); border-radius: 6px;
                  font-size: 13px; background: #fff; }
  main { padding: 0 24px 40px; }
  .case { background: #fff; border: 1px solid var(--line); border-radius: 8px;
          margin-bottom: 8px; overflow: hidden; }
  .case-head { padding: 10px 14px; cursor: pointer; display: flex; gap: 10px;
               align-items: center; font-size: 13px; }
  .case-head:hover { background: #f1f5f9; }
  .badge { font-size: 11px; padding: 2px 8px; border-radius: 10px; color: #fff; }
  .b-pass { background: var(--ok); } .b-fail { background: var(--bad); }
  .b-split { background: #64748b; } .b-code { background: #b45309; margin-right: 4px; }
  .case-body { display: none; border-top: 1px solid var(--line); padding: 12px 14px; }
  .case.open .case-body { display: block; }
  .trial { border: 1px solid var(--line); border-radius: 6px; margin: 8px 0; }
  .trial-head { padding: 6px 10px; font-size: 12px; background: #f1f5f9; cursor: pointer; }
  .trial-body { display: none; padding: 10px; font-size: 12.5px; }
  .trial.open .trial-body { display: block; }
  .msg { margin: 6px 0; padding: 6px 10px; border-radius: 6px; white-space: pre-wrap;
         word-break: break-word; }
  .m-user { background: #eff6ff; } .m-assistant { background: #f0fdf4; }
  .m-tool { background: #fff7ed; font-family: ui-monospace, monospace; font-size: 12px; }
  .m-system { background: #f5f5f4; color: var(--muted); max-height: 120px; overflow: auto; }
  .toolcall { border-left: 3px solid #f59e0b; padding: 4px 10px; margin: 6px 0;
              font-family: ui-monospace, monospace; font-size: 12px; white-space: pre-wrap;
              word-break: break-all; }
  .toolcall.err { border-color: var(--bad); }
  .verdict { margin: 4px 0; padding: 6px 10px; border-radius: 6px; background: #f8fafc;
             border: 1px solid var(--line); }
  .verdict.fail { border-color: #fecaca; background: #fef2f2; }
  details { margin: 4px 0; }
</style>
</head>
<body>
<header>
  <h1>AgentEval Trace Viewer</h1>
  <div class="meta">run: __RUN_ID__ ｜ target: __TARGET__ ｜ model: __MODEL__ ｜ prompt: __PROMPT__ ｜ dataset: __DSHASH__</div>
</header>
__BANNER__
<div class="cards" id="cards"></div>
<div class="filters">
  <select id="f-split"><option value="">全部 split</option></select>
  <select id="f-result"><option value="">全部结果</option><option value="pass">通过</option><option value="fail">失败</option></select>
  <select id="f-code"><option value="">全部 reason code</option></select>
  <input id="f-search" placeholder="搜索 case_id / 问题关键词">
</div>
<main id="cases"></main>
<script id="run-data" type="application/json">__DATA__</script>
<script>
const RUN = JSON.parse(document.getElementById('run-data').textContent);
const $ = s => document.querySelector(s);
function esc(s){ return String(s ?? '').replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }

// 摘要卡片
(function(){
  const o = RUN.summary.overall;
  const cards = [
    ['pass@1', (o.pass_at_1*100).toFixed(1) + '%'],
    ['pass^k', (o.pass_at_k*100).toFixed(1) + '%'],
    ['用例 × trials', o.n_cases + ' × ' + o.trials_per_case],
    ['失败 trial', RUN.trials.filter(t=>!t.passed).length],
    ['工具错误率', (o.tool_error_rate*100).toFixed(1) + '%'],
    ['基础设施错误率', (o.infra_error_rate*100).toFixed(1) + '%'],
    ['总成本($)', o.total_cost_usd.toFixed(4)],
    ['延迟 p95(ms)', o.latency_ms.p95.toFixed(0)],
  ];
  $('#cards').innerHTML = cards.map(([k,v]) =>
    `<div class="card"><div class="v">${esc(v)}</div><div class="k">${esc(k)}</div></div>`).join('');
})();

// 筛选项初始化
const splits = [...new Set(RUN.cases.map(c=>c.split))];
$('#f-split').innerHTML += splits.map(s=>`<option>${esc(s)}</option>`).join('');
const codes = Object.keys(RUN.summary.reason_codes || {});
$('#f-code').innerHTML += codes.map(c=>`<option>${esc(c)}</option>`).join('');

function caseTrials(cid){ return RUN.trials.filter(t=>t.case_id===cid); }

function render(){
  const fs = $('#f-split').value, fr = $('#f-result').value, fc = $('#f-code').value;
  const kw = $('#f-search').value.trim();
  const byCid = {};
  RUN.trials.forEach(t => { (byCid[t.case_id] = byCid[t.case_id] || []).push(t); });
  const html = [];
  for (const c of RUN.cases) {
    if (fs && c.split !== fs) continue;
    if (fr === 'pass' && !c.pass) continue;
    if (fr === 'fail' && c.pass) continue;
    if (fc && !(c.reason_codes||[]).includes(fc)) continue;
    if (kw && !c.case_id.includes(kw) && !(c.question||'').includes(kw)) continue;
    const trials = byCid[c.case_id] || [];
    const badges = c.pass ? '<span class="badge b-pass">PASS</span>' : '<span class="badge b-fail">FAIL</span>';
    const codeBadges = (c.reason_codes||[]).map(x=>`<span class="badge b-code">${esc(x)}</span>`).join('');
    html.push(`<div class="case"><div class="case-head" onclick="this.parentNode.classList.toggle('open')">
      ${badges}<span class="badge b-split">${esc(c.split)}</span>
      <b>${esc(c.case_id)}</b><span style="color:#6b7280">${esc((c.question||'').slice(0,60))}</span>
      ${codeBadges}<span style="margin-left:auto">${c.passed_trials}/${c.total_trials}</span></div>
      <div class="case-body">${trials.map(renderTrial).join('')}</div></div>`);
  }
  $('#cases').innerHTML = html.join('') || '<p style="color:#6b7280">无匹配 case</p>';
}

function renderTrial(t){
  const v = Object.entries(t.verdicts||{}).map(([g, vd]) => {
    if (vd.passed === null) return `<div class="verdict">⏭ <b>${esc(g)}</b> 跳过：${esc(vd.detail)}</div>`;
    const cls = vd.passed ? 'verdict' : 'verdict fail';
    const icon = vd.passed ? '✅' : '❌';
    const codes = (vd.reason_codes||[]).join(',');
    return `<div class="${cls}">${icon} <b>${esc(g)}</b> ${codes?('['+esc(codes)+'] '):''}${esc(vd.detail)}</div>`;
  }).join('');
  const msgs = (t.messages||[]).map(m =>
    `<div class="msg m-${esc(m.role)}"><b>${esc(m.role)}</b>: ${esc((m.content||'').slice(0,1500))}</div>`).join('');
  const tools = (t.tool_calls||[]).map(tc =>
    `<div class="toolcall ${tc.error?'err':''}">🔧 ${esc(tc.name)}(${esc(JSON.stringify(tc.arguments).slice(0,300))})
${tc.error ? ('ERROR: '+esc(tc.error)+(tc.blocked?' [被沙箱拦截]':'')) : esc((tc.result||'').slice(0,800))}</div>`).join('');
  return `<div class="trial"><div class="trial-head" onclick="this.parentNode.classList.toggle('open')">
    trial #${t.trial_index} ｜ ${t.passed?'✅ 通过':'❌ 失败'} ｜ ${t.duration_ms.toFixed(0)}ms ｜ steps=${t.steps} ｜ tokens=${(t.usage||{}).total_tokens||0}${t.infra_error?(' ｜ E15: '+esc(t.infra_error)):''}
    </div><div class="trial-body">
    <details><summary>消息流（${(t.messages||[]).length}）</summary>${msgs}</details>
    <details><summary>工具调用（${(t.tool_calls||[]).length}）</summary>${tools||'（无）'}</details>
    <details open><summary>最终回答</summary><div class="msg m-assistant">${esc((t.final_answer||'').slice(0,2000))}</div></details>
    <div>${v}</div></div></div>`;
}

['f-split','f-result','f-code','f-search'].forEach(id =>
  document.getElementById(id).addEventListener('input', render));
render();
</script>
</body>
</html>
"""


def generate_viewer(run_dir: str | Path, out_path: str | Path) -> Path:
    """生成单文件 HTML Viewer，返回输出路径。"""
    run_dir = Path(run_dir)
    meta, traces, summary = load_run(run_dir)

    # 精简传输结构（Viewer 前端直接消费）
    case_questions = {}
    for t in traces:
        if t.case_id not in case_questions:
            q = next((m.get("content", "") for m in t.messages
                      if m.get("role") == "user"), "")
            case_questions[t.case_id] = q

    data = {
        "run_id": meta.get("run_id", run_dir.name),
        "meta": meta,
        "summary": summary,
        "cases": [{**c, "question": case_questions.get(c["case_id"], "")}
                  for c in summary["cases"]],
        "trials": [t.to_dict() for t in traces],
    }
    # JSON 注入 HTML 安全：转义 </ 防止 </script> 提前闭合
    data_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")

    is_mock = str(meta.get("target", "")).startswith("mock")
    banner = ('<div class="banner">⚠️ Mock 演示数据：本 run 由确定性 Mock Target 生成，'
              '仅演示框架能力，不代表真实模型表现。</div>') if is_mock else ""

    html = (_HTML_TEMPLATE
            .replace("__RUN_ID__", str(meta.get("run_id", run_dir.name)))
            .replace("__TARGET__", str(meta.get("target", "-")))
            .replace("__MODEL__", str(meta.get("model") or "-"))
            .replace("__PROMPT__", str(meta.get("prompt_version") or "-"))
            .replace("__DSHASH__", str(meta.get("dataset_hash", "-")))
            .replace("__BANNER__", banner)
            .replace("__DATA__", data_json))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path
