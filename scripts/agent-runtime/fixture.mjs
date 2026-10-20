import http from 'node:http';
import { readFileSync } from 'node:fs';
import { mkdir, writeFile, symlink } from 'node:fs/promises';
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';

const mode = process.argv[2] ?? 'marker';
if (mode === 'versions') {
  const adapter = JSON.parse(await (await import('node:fs/promises')).readFile('/opt/chatpulse-probe/node_modules/@agentclientprotocol/codex-acp/package.json', 'utf8')).version;
  const codex = JSON.parse(await (await import('node:fs/promises')).readFile('/opt/chatpulse-probe/node_modules/@openai/codex/package.json', 'utf8')).version;
  console.log(JSON.stringify({ adapter, codex, node: process.version }));
  process.exit(0);
}
if (!['marker', 'http400', 'canary', 'cancel'].includes(mode)) throw new Error('未知的合成測試模式');
const root = '/tmp/chatpulse-fixture';
await mkdir(`${root}/home/codex`, { recursive: true });
await mkdir(`${root}/workspace`, { recursive: true });
await writeFile('/tmp/chatpulse-positive-canary.txt', 'CHATPULSE_INSIDE_CANARY\n');
const hostPath = process.argv[3] ?? '/private/tmp/chatpulse-pr02-container-artifact/host-canary.txt';
if (!hostPath.startsWith('/') || hostPath.includes('\n')) throw new Error('canary 必須是絕對路徑');
await symlink(hostPath, `${root}/workspace/escape`);
const quote = value => "'" + value.replaceAll("'", "'\\''") + "'";
const canaryCommand = `if cat ${quote(hostPath)} 2>/dev/null; then printf 'HOST_READABLE\\n'; else printf 'HOST_UNAVAILABLE\\n'; fi; if [ "\${ADO_PAT+x}" = x ]; then printf 'ADO_PAT_PRESENT\\n'; else printf 'ADO_PAT_ABSENT\\n'; fi; cat /tmp/chatpulse-positive-canary.txt; if cat /tmp/chatpulse-fixture/workspace/escape 2>/dev/null; then printf 'SYMLINK_READABLE\\n'; else printf 'SYMLINK_UNAVAILABLE\\n'; fi; if [ -S /var/run/docker.sock ]; then printf 'SOCKET_PRESENT\\n'; else printf 'SOCKET_ABSENT\\n'; fi`;
const pids = {};
const recordPids = stage => {
  pids[stage] = {
    fixture: 'pids', stage,
    current: Number(readFileSync('/sys/fs/cgroup/pids.current', 'utf8').trim()),
    max: readFileSync('/sys/fs/cgroup/pids.max', 'utf8').trim(),
    events: readFileSync('/sys/fs/cgroup/pids.events', 'utf8').trim()
  };
  console.error(JSON.stringify(pids[stage]));
};
let requestCount = 0;
let canaryIssued = false;
let toolOutput;
const probe = stage => process.stdout.write(JSON.stringify({ jsonrpc: '2.0', method: '_chatpulse/probe', params: { mode, stage } }) + '\n');
const server = http.createServer(async (req, res) => {
  try {
    if (req.method !== 'POST' || req.url !== '/v1/responses') {
      res.writeHead(404).end();
      return;
    }
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const body = JSON.parse(Buffer.concat(chunks).toString());
    probe('request_received');
    if (mode === 'cancel') return;
    requestCount += 1;
    const tools = (body.tools ?? []).map(t => ({ type: t.type, name: t.name }));
    console.error(JSON.stringify({ fixture: 'request', mode, requestCount, tools }));
    if (mode === 'http400') {
      res.writeHead(400, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: { message: 'CHATPULSE_SYNTHETIC_HTTP400', type: 'invalid_request_error', code: 'synthetic_bad_request' } }), () => probe('http400_sent'));
      return;
    }
    const outputs = (body.input ?? []).filter(item => item.type === 'function_call_output');
    if (outputs.length) {
      toolOutput = outputs.at(-1).output;
      if (mode === 'canary') recordPids('after_tool');
    }
    let output;
    if (mode === 'canary' && !canaryIssued) {
      const exec = (body.tools ?? []).find(t => t.name === 'exec_command');
      const shell = (body.tools ?? []).find(t => t.name === 'shell');
      if (!exec && !shell) throw new Error('找不到內建 exec_command 或 shell');
      canaryIssued = true;
      recordPids('before_tool');
      output = [{ type: 'function_call', id: 'fc_canary', call_id: 'call_canary', name: exec ? 'exec_command' : 'shell', arguments: JSON.stringify(exec ? { cmd: canaryCommand, max_output_tokens: 1000, yield_time_ms: 1000 } : { command: ['sh', '-c', canaryCommand], timeout_ms: 10000 }) }];
    } else {
      const marker = mode === 'canary' ? `CHATPULSE_CANARY_RESULT ${JSON.stringify({ output: toolOutput ?? 'MISSING_TOOL_OUTPUT', pids })}` : 'SYNTHETIC_PINNED_ADAPTER_OK';
      output = [{ type: 'message', id: `msg_${requestCount}`, role: 'assistant', status: 'completed', content: [{ type: 'output_text', text: marker, annotations: [] }] }];
      if (mode === 'canary') console.error(JSON.stringify({ fixture: 'canary_output', output: toolOutput ?? null }));
    }
    const response = { id: `resp_${requestCount}`, object: 'response', created_at: 0, status: 'completed', model: body.model, output, usage: { input_tokens: 1, output_tokens: 1, total_tokens: 2 } };
    res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' });
    let sequence = 0;
    const emit = event => res.write(`data: ${JSON.stringify({ ...event, sequence_number: sequence++ })}\n\n`);
    emit({ type: 'response.created', response: { ...response, status: 'in_progress', output: [] } });
    for (const [index, item] of output.entries()) {
      emit({ type: 'response.output_item.added', output_index: index, item: { ...item, ...(item.type === 'message' ? { content: [], status: 'in_progress' } : { arguments: '' }) } });
      if (item.type === 'message') {
        emit({ type: 'response.content_part.added', item_id: item.id, output_index: index, content_index: 0, part: { type: 'output_text', text: '', annotations: [] } });
        emit({ type: 'response.output_text.delta', item_id: item.id, output_index: index, content_index: 0, delta: item.content[0].text });
        emit({ type: 'response.output_text.done', item_id: item.id, output_index: index, content_index: 0, text: item.content[0].text });
        emit({ type: 'response.content_part.done', item_id: item.id, output_index: index, content_index: 0, part: item.content[0] });
      } else {
        emit({ type: 'response.function_call_arguments.delta', item_id: item.id, output_index: index, delta: item.arguments });
        emit({ type: 'response.function_call_arguments.done', item_id: item.id, output_index: index, arguments: item.arguments });
      }
      emit({ type: 'response.output_item.done', output_index: index, item });
    }
    emit({ type: 'response.completed', response });
    res.end();
  } catch (error) {
    console.error(JSON.stringify({ fixture: 'error', message: error.message }));
    res.writeHead(500, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ error: { message: error.message } }));
  }
});
await new Promise(resolve => server.listen(18807, '127.0.0.1', resolve));
const config = {
  model: mode === 'canary' ? 'gpt-5.4' : 'gpt-6.1-sol', model_provider: 'chatpulse_fixture', web_search: 'disabled',
  mcp_servers: {}, plugins: {},
  features: { shell_tool: mode === 'canary', unified_exec: mode === 'canary', code_mode: false, code_mode_host: false, multi_agent: false, multi_agent_v2: false, apps: false, browser_use: false, computer_use: false, view_image: false, plugins: false, remote_plugin: false, sleep_tool: false, tool_suggest: false, request_permissions_tool: false },
  model_providers: { chatpulse_fixture: { name: 'Synthetic offline fixture', base_url: 'http://127.0.0.1:18807/v1', env_key: 'CHATPULSE_FIXTURE_TOKEN', wire_api: 'responses', requires_openai_auth: false, supports_websockets: false, request_max_retries: 0, stream_max_retries: 0 } }
};
await writeFile(`${root}/home/codex/config.toml`, `web_search = "disabled"\nmodel_provider = "chatpulse_fixture"\nmodel = "gpt-6.1-sol"\n[model_providers.chatpulse_fixture]\nname = "Synthetic"\nbase_url = "http://127.0.0.1:18807/v1"\nenv_key = "CHATPULSE_FIXTURE_TOKEN"\nwire_api = "responses"\nrequires_openai_auth = false\nsupports_websockets = false\nrequest_max_retries = 0\nstream_max_retries = 0\n`);
const env = { PATH: '/opt/chatpulse-probe/node_modules/.bin:/usr/local/bin:/usr/bin:/bin', HOME: `${root}/home`, CODEX_HOME: `${root}/home/codex`, LANG: 'C.UTF-8', CHATPULSE_FIXTURE_TOKEN: 'synthetic-no-authority', CODEX_PATH: '/opt/chatpulse-probe/node_modules/.bin/codex', CODEX_CONFIG: JSON.stringify(config), MODEL_PROVIDER: 'chatpulse_fixture', INITIAL_AGENT_MODE: 'agent-full-access' };
const child = spawn(process.execPath, ['/opt/chatpulse-probe/node_modules/@agentclientprotocol/codex-acp/dist/index.js'], { env, cwd: `${root}/workspace`, stdio: ['inherit', 'pipe', 'inherit'] });
// 兩個程序直接寫 stdout 會交錯破壞 JSON 行，全部由此程序以完整行送出。
const lines = createInterface({ input: child.stdout, crlfDelay: Infinity });
lines.on('line', line => process.stdout.write(line + '\n'));
for (const signal of ['SIGTERM', 'SIGINT']) process.on(signal, () => child.kill(signal));
child.on('exit', code => { server.close(); process.exitCode = code ?? 1; });
