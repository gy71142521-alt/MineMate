const mineflayer = require('mineflayer');
const { createAuthOptions } = require('./auth');
const { registerChatEvents } = require('./events/chat');
const { createMovementController } = require('./movement');
const { createPerception } = require('./perception');

function describeError(error, auth) {
  // 只用原始错误做分类，不输出可能包含令牌的认证响应、请求头或堆栈。
  const errors = [error, error?.cause, ...(error?.errors ?? [])].filter(Boolean);
  const detail = errors.map((item) => `${item.code ?? ''} ${item.message ?? ''}`).join(' ');
  if (/ECONNREFUSED/i.test(detail)) return '连接失败：目标拒绝连接。请确认世界已开放 LAN，并更新 MC_PORT。';
  if (/ENOTFOUND|EAI_AGAIN/i.test(detail)) return '连接失败：无法解析主机。请检查 MC_HOST 和网络/DNS。';
  if (/ETIMEDOUT|ESOCKETTIMEDOUT|timed? ?out/i.test(detail)) return '连接超时：请检查 LAN 端口、防火墙和网络；Microsoft 模式还需要访问认证服务。';
  if (/ECONNRESET|EPIPE|ECONNABORTED/i.test(detail)) return '连接中断：远端关闭了连接，请检查 Minecraft 是否仍在运行。';
  if (/(?:unsupported|unrecognized|unknown).*?(?:version|protocol)|version.*not supported|protocol.*version|version.*protocol/i.test(detail)) {
    return '版本或协议不兼容：请核对 Minecraft 版本与当前 Mineflayer 支持范围。';
  }
  if (auth === 'microsoft' && /microsoft|xbox|xsts|oauth|authenticat|invalid_grant|device.?code|access_denied|expired_token|entitlement|profile data|minecraft.*profile|does not own|401|403/i.test(detail)) {
    return 'Microsoft 认证失败：请检查设备登录是否完成、是否过期，账号是否拥有 Java Edition 及多人游戏权限，并确认认证网络可用。';
  }
  return '登录或连接失败：请检查认证模式、Minecraft 版本与 LAN 配置；Microsoft 模式还需核对账号权限及认证网络。';
}

function formatReason(reason) {
  let value;
  try {
    value = typeof reason === 'string' ? reason : JSON.stringify(reason);
  } catch {
    value = '服务器未提供可读原因';
  }
  // 记录服务器原因，但不直接转储 Error/session 等认证对象。
  return (value || '未提供原因')
    .replace(/[\r\n\u0000-\u001f]/g, ' ')
    .replace(/\bBearer\s+\S+/gi, 'Bearer [已隐藏]')
    .replace(/((?:access[_-]?token|refresh[_-]?token|client[_-]?token|password|api[_-]?key)["']?\s*[:=]\s*["']?)[^\s,"'}]+/gi, '$1[已隐藏]');
}

function startBot(config, { createBot = mineflayer.createBot, createMovement = createMovementController, createPerceptionModule = createPerception, logger = console, onFailure = () => {} } = {}) {
  logger.log(`[MineMate] 正在连接服务器 ${config.host}:${config.port}（认证：${config.auth}）...`);
  const options = {
    host: config.host,
    port: config.port,
    ...createAuthOptions(config, logger),
    // 由应用处理错误，避免底层默认日志输出完整认证对象。
    hideErrors: true
  };

  let bot;
  try {
    bot = createBot(options);
  } catch (error) {
    logger.error(`[MineMate] ${describeError(error, config.auth)}`);
    onFailure();
    return null;
  }

  let spawned = false;
  let failed = false;
  let connectionState = 'connecting';
  bot.mineMateStatus = () => ({ connected: connectionState === 'connected', spawned, state: connectionState });
  const fail = () => {
    if (!failed) onFailure();
    failed = true;
  };

  bot.once('spawn', () => {
    spawned = true;
    connectionState = 'connected';
    logger.log('[MineMate] 成功进入游戏');
  });
  bot.on('error', (error) => {
    if (!spawned) connectionState = 'error';
    logger.error(`[MineMate] ${describeError(error, config.auth)}`);
    fail();
  });
  bot.on('kicked', (reason, loggedIn) => {
    connectionState = 'kicked';
    logger.error(`[MineMate] ${loggedIn ? '被服务器踢出' : '登录被服务器拒绝'}：${formatReason(reason)}`);
    logger.error(`[MineMate] 请核对 MC_AUTH、账号/昵称是否与玩家冲突及多人游戏权限。${config.auth === 'offline' ? 'Offline 无法连接要求正版认证的目标。' : '如会话失效，请重新完成 Microsoft 登录。'}`);
    fail();
  });
  bot.on('end', (reason) => {
    connectionState = 'disconnected';
    logger.log(`[MineMate] 连接已断开：${formatReason(reason)}。不会自动重连。`);
    if (!spawned) fail();
  });

  let movement;
  try {
    movement = createMovement(bot, { ownerUsername: config.ownerUsername, pathfinding: config.pathfinding, logger });
    bot.movement = movement;
  } catch {
    logger.error('[Movement] 移动插件加载失败，移动指令不可用；请检查依赖与游戏版本。');
    fail();
  }
  let perception;
  try {
    perception = createPerceptionModule(bot, { movement, config: config.perception, logger });
    // 复用此实例供后续调用快照，避免重复创建模块和事件监听器。
    bot.perception = perception;
  } catch {
    logger.error('[Perception] 感知模块初始化失败，查询将提示暂不可用。');
  }
  registerChatEvents(bot, logger, movement, perception);
  return bot;
}

module.exports = { startBot, describeError, formatReason };
