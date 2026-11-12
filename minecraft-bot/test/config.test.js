const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { parseConfig, DEFAULT_PATHFINDING, DEFAULT_PERCEPTION, DEFAULT_HTTP } = require('../src/config');
const { createAuthOptions } = require('../src/auth');

const base = { MC_PORT: '51234', BOT_USERNAME: 'MineMate' };

test('默认使用本机和 Microsoft，端口必须来自配置', () => {
  assert.deepEqual(parseConfig(base), {
    host: '127.0.0.1', port: 51234, username: 'MineMate', auth: 'microsoft', ownerUsername: '',
    pathfinding: DEFAULT_PATHFINDING,
    perception: DEFAULT_PERCEPTION,
    http: DEFAULT_HTTP
  });
  assert.equal(parseConfig({ ...base, MC_PORT: '60000' }).port, 60000);
  assert.equal(parseConfig({ ...base, MC_HOST: '192.168.1.8' }).host, '192.168.1.8');
});

test('HTTP 服务地址、端口和请求体上限可配置', () => {
  assert.deepEqual(parseConfig({ ...base, HTTP_HOST: '0.0.0.0', HTTP_PORT: '8123', HTTP_MAX_BODY_BYTES: '32768' }).http, {
    host: '0.0.0.0', port: 8123, maxBodyBytes: 32768
  });
  assert.equal(parseConfig({ ...base, HTTP_PORT: '0' }).http.port, 0);
  for (const [name, value] of [['HTTP_PORT', '-1'], ['HTTP_PORT', '65536'], ['HTTP_PORT', 'abc'], ['HTTP_MAX_BODY_BYTES', '0'], ['HTTP_MAX_BODY_BYTES', '1048577']]) {
    assert.throws(() => parseConfig({ ...base, [name]: value }), new RegExp(name));
  }
});

test('感知半径、结果数量及缓存时长有默认值和明确上限', () => {
  assert.deepEqual(parseConfig({ ...base, PERCEPTION_ENTITY_RADIUS: '24', PERCEPTION_BLOCK_RADIUS: '4', PERCEPTION_MAX_ENTITIES: '48', PERCEPTION_MAX_BLOCKS: '96', PERCEPTION_BLOCK_CACHE_MS: '2500' }).perception, {
    entityRadius: 24, blockRadius: 4, maxEntities: 48, maxBlocks: 96, blockCacheMs: 2500
  });
  for (const [name, values] of [
    ['PERCEPTION_ENTITY_RADIUS', ['', '0', '65', '1.5']],
    ['PERCEPTION_BLOCK_RADIUS', ['', '0', '6', '2.5']],
    ['PERCEPTION_MAX_ENTITIES', ['', '0', '129', '1.5']],
    ['PERCEPTION_MAX_BLOCKS', ['', '0', '257', '1.5']],
    ['PERCEPTION_BLOCK_CACHE_MS', ['', '0', '10001', '1.5']]
  ]) {
    for (const value of values) {
      assert.throws(() => parseConfig({ ...base, [name]: value }), new RegExp(name));
    }
  }
});

test('寻路距离、搜索范围及时限分别可配置且有界', () => {
  assert.deepEqual(parseConfig({ ...base, PATHFINDER_MAX_DISTANCE: '384', PATHFINDER_SEARCH_RADIUS: '512', PATHFINDER_THINK_TIMEOUT_MS: '7000' }).pathfinding, {
    maxDistance: 384, searchRadius: 512, thinkTimeout: 7000
  });
  for (const [name, values] of [
    ['PATHFINDER_MAX_DISTANCE', ['', '0', '-1', '4097', '1.5', 'abc']],
    ['PATHFINDER_SEARCH_RADIUS', ['', '0', '4097', '2.5', 'abc']],
    ['PATHFINDER_THINK_TIMEOUT_MS', ['', '0', '60001', '5.5', 'abc']]
  ]) {
    for (const value of values) {
      assert.throws(() => parseConfig({ ...base, [name]: value }), new RegExp(name));
    }
  }
});

test('主人配置可选，填写时校验游戏昵称', () => {
  assert.equal(parseConfig({ ...base, OWNER_USERNAME: ' Steve ' }).ownerUsername, 'Steve');
  assert.equal(parseConfig({ ...base, OWNER_USERNAME: ' ' }).ownerUsername, '');
  for (const owner of ['ab', 'abcdefghijklmnopq', 'name space', 'owner@example.com']) {
    assert.throws(() => parseConfig({ ...base, OWNER_USERNAME: owner }), /OWNER_USERNAME/);
  }
});

test('拒绝缺少、越界、小数及混杂字符端口', () => {
  for (const port of [undefined, '', ' ', '0', '-1', '65536', '123.4', '123abc', '1e3']) {
    assert.throws(() => parseConfig({ ...base, MC_PORT: port }), /MC_PORT/);
  }
  for (const port of ['1', '65535']) {
    assert.equal(parseConfig({ ...base, MC_PORT: port }).port, Number(port));
  }
});

test('拒绝空主机、空账号和未知认证模式', () => {
  assert.throws(() => parseConfig({ ...base, MC_HOST: ' ' }), /MC_HOST/);
  assert.throws(() => parseConfig({ ...base, BOT_USERNAME: '' }), /BOT_USERNAME/);
  for (const auth of ['', 'mojang', 'auto', 'invalid']) {
    assert.throws(() => parseConfig({ ...base, MC_AUTH: auth }), /MC_AUTH/);
  }
});

test('显式 Offline 配置校验游戏昵称，Microsoft 可使用账号标识', () => {
  assert.equal(parseConfig({ ...base, MC_AUTH: ' OFFLINE ' }).auth, 'offline');
  for (const username of ['ab', 'abcdefghijklmnopq', '中文昵称', 'name space', 'a@b.com']) {
    assert.throws(() => parseConfig({ ...base, MC_AUTH: 'offline', BOT_USERNAME: username }), /BOT_USERNAME/);
  }
  assert.equal(parseConfig({ ...base, BOT_USERNAME: 'bot-account@example.com' }).username, 'bot-account@example.com');
});

test('统一认证接口只为 Microsoft 设置专用缓存和设备登录回调', () => {
  const logs = [];
  const options = createAuthOptions(parseConfig(base), { log: (message) => logs.push(message) });
  assert.equal(options.auth, 'microsoft');
  assert.equal(options.username, 'MineMate');
  assert.equal(options.profilesFolder, path.resolve(__dirname, '..', '.auth-cache'));
  assert.equal('password' in options, false);
  assert.equal('session' in options, false);
  options.onMsaCode({
    verification_uri: 'https://www.microsoft.com/link', user_code: 'TEST-CODE',
    access_token: 'secret-test-access-token', refresh_token: 'secret-test-refresh-token'
  });
  assert.match(logs.join('\n'), /TEST-CODE/);
  assert.doesNotMatch(logs.join('\n'), /secret-test/);
  assert.deepEqual(createAuthOptions(parseConfig({ ...base, MC_AUTH: 'offline' })), {
    auth: 'offline', username: 'MineMate'
  });
  assert.throws(() => createAuthOptions({ auth: 'auto' }), /不支持/);
});
