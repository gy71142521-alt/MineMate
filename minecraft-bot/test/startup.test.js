const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const os = require('node:os');
const net = require('node:net');
const { spawnSync } = require('node:child_process');

const entry = path.resolve(__dirname, '..', 'src', 'index.js');
const env = {
  ...process.env,
  MC_HOST: '127.0.0.1',
  MC_AUTH: 'offline',
  BOT_USERNAME: 'MineMate',
  OWNER_USERNAME: '',
  PATHFINDER_MAX_DISTANCE: '256',
  PATHFINDER_SEARCH_RADIUS: '256',
  PATHFINDER_THINK_TIMEOUT_MS: '5000',
  PERCEPTION_ENTITY_RADIUS: '16',
  PERCEPTION_BLOCK_RADIUS: '5',
  PERCEPTION_MAX_ENTITIES: '32',
  PERCEPTION_MAX_BLOCKS: '64',
  PERCEPTION_BLOCK_CACHE_MS: '1000'
};

test('真实启动入口拒绝空端口，非零退出且不尝试连接', () => {
  const result = spawnSync(process.execPath, [entry], {
    cwd: os.tmpdir(), env: { ...env, MC_PORT: '' }, encoding: 'utf8', timeout: 10000
  });
  assert.ifError(result.error);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /MC_PORT/);
  assert.equal(result.stdout, '');
});

test('真实 Mineflayer 连接未监听的本机端口，清晰报错并退出，不自动重连', async () => {
  // 操作系统分配临时端口后关闭；只测试回环连接失败，不需要 Minecraft 或外网。
  const server = net.createServer();
  const port = await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const assigned = server.address().port;
      server.close((error) => error ? reject(error) : resolve(assigned));
    });
  });
  const result = spawnSync(process.execPath, [entry], {
    cwd: os.tmpdir(), env: { ...env, MC_PORT: String(port) }, encoding: 'utf8', timeout: 15000
  });
  assert.ifError(result.error);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /目标拒绝连接/);
  assert.doesNotMatch(result.stderr, /释放移动控制失败|移动插件加载失败/);
  assert.match(result.stdout, /连接已断开/);
  assert.equal((result.stdout.match(/正在连接服务器/g) ?? []).length, 1);
});
