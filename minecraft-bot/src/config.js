const path = require('node:path');
const dotenv = require('dotenv');

const DEFAULT_PATHFINDING = Object.freeze({ maxDistance: 256, searchRadius: 256, thinkTimeout: 5000 });
const DEFAULT_PERCEPTION = Object.freeze({
  entityRadius: 16,
  blockRadius: 5,
  maxEntities: 32,
  maxBlocks: 64,
  blockCacheMs: 1000
});
const DEFAULT_HTTP = Object.freeze({ host: '127.0.0.1', port: 3000, maxBodyBytes: 16 * 1024 });

function readPositiveInteger(env, name, fallback, maximum) {
  const value = env[name] === undefined ? String(fallback) : env[name].trim();
  if (!/^\d+$/.test(value) || !Number.isSafeInteger(Number(value)) || Number(value) < 1 || Number(value) > maximum) {
    throw new Error(`${name} 必须为 1–${maximum} 的整数。`);
  }
  return Number(value);
}

function readPort(env, name, fallback, allowZero = false) {
  const value = env[name] === undefined ? String(fallback) : env[name].trim();
  const minimum = allowZero ? 0 : 1;
  if (!/^\d+$/.test(value) || !Number.isSafeInteger(Number(value)) || Number(value) < minimum || Number(value) > 65535) {
    throw new Error(`${name} 必须为 ${minimum}–65535 的整数。`);
  }
  return Number(value);
}

// LAN 端口没有固定默认值，必须使用本次开放局域网显示的端口。
function parseConfig(env) {
  const host = (env.MC_HOST ?? '127.0.0.1').trim();
  const portText = (env.MC_PORT ?? '').trim();
  const username = (env.BOT_USERNAME ?? '').trim();
  const auth = (env.MC_AUTH ?? 'microsoft').trim().toLowerCase();
  const ownerUsername = (env.OWNER_USERNAME ?? '').trim();
  const httpHost = (env.HTTP_HOST ?? DEFAULT_HTTP.host).trim();

  if (!host) throw new Error('MC_HOST 不能为空；本机请填写 127.0.0.1。');
  if (!httpHost) throw new Error('HTTP_HOST 不能为空。');
  if (!/^\d+$/.test(portText) || Number(portText) < 1 || Number(portText) > 65535) {
    throw new Error('MC_PORT 必须填写本次 Open to LAN 的实际端口（1–65535 的整数）。');
  }
  if (!['microsoft', 'offline'].includes(auth)) {
    throw new Error('MC_AUTH 仅支持 microsoft 或 offline，不会自动降级认证模式。');
  }
  if (!username) throw new Error('BOT_USERNAME 不能为空。');
  if (auth === 'offline' && !/^[A-Za-z0-9_]{3,16}$/.test(username)) {
    throw new Error('Offline 模式的 BOT_USERNAME 必须为 3–16 位英文字母、数字或下划线。');
  }

  if (ownerUsername && !/^[A-Za-z0-9_]{3,16}$/.test(ownerUsername)) {
    throw new Error('OWNER_USERNAME 须为空或 3–16 位英文字母、数字、下划线组成的游戏昵称。');
  }

  const pathfinding = {
    maxDistance: readPositiveInteger(env, 'PATHFINDER_MAX_DISTANCE', DEFAULT_PATHFINDING.maxDistance, 4096),
    searchRadius: readPositiveInteger(env, 'PATHFINDER_SEARCH_RADIUS', DEFAULT_PATHFINDING.searchRadius, 4096),
    thinkTimeout: readPositiveInteger(env, 'PATHFINDER_THINK_TIMEOUT_MS', DEFAULT_PATHFINDING.thinkTimeout, 60000)
  };

  const perception = {
    entityRadius: readPositiveInteger(env, 'PERCEPTION_ENTITY_RADIUS', DEFAULT_PERCEPTION.entityRadius, 64),
    blockRadius: readPositiveInteger(env, 'PERCEPTION_BLOCK_RADIUS', DEFAULT_PERCEPTION.blockRadius, 5),
    maxEntities: readPositiveInteger(env, 'PERCEPTION_MAX_ENTITIES', DEFAULT_PERCEPTION.maxEntities, 128),
    maxBlocks: readPositiveInteger(env, 'PERCEPTION_MAX_BLOCKS', DEFAULT_PERCEPTION.maxBlocks, 256),
    blockCacheMs: readPositiveInteger(env, 'PERCEPTION_BLOCK_CACHE_MS', DEFAULT_PERCEPTION.blockCacheMs, 10000)
  };

  const http = {
    host: httpHost,
    // 0 仅表示让操作系统选择临时端口，便于测试；默认服务端口为 3000。
    port: readPort(env, 'HTTP_PORT', DEFAULT_HTTP.port, true),
    maxBodyBytes: readPositiveInteger(env, 'HTTP_MAX_BODY_BYTES', DEFAULT_HTTP.maxBodyBytes, 1024 * 1024)
  };

  return { host, port: Number(portText), username, auth, ownerUsername, pathfinding, perception, http };
}

function loadConfig() {
  // 固定相对于模块读取，避免启动时工作目录不同而读错配置。
  const result = dotenv.config({ path: path.join(__dirname, '..', '.env'), quiet: true });
  if (result.error && result.error.code !== 'ENOENT') {
    throw new Error('无法读取 minecraft-bot/.env，请检查文件权限。');
  }
  return parseConfig(process.env);
}

module.exports = { parseConfig, loadConfig, DEFAULT_PATHFINDING, DEFAULT_PERCEPTION, DEFAULT_HTTP };
