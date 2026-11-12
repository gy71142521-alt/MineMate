const path = require('node:path');

// 两种认证共用一个入口；不接收密码、令牌，也不在失败时切换模式。
function createAuthOptions(config, logger = console) {
  if (!['microsoft', 'offline'].includes(config.auth)) {
    throw new Error('不支持的认证模式，请检查 MC_AUTH。');
  }

  const options = { auth: config.auth, username: config.username };
  if (config.auth === 'microsoft') {
    options.profilesFolder = path.join(__dirname, '..', '.auth-cache');
    options.onMsaCode = (data) => {
      // user_code 是一次性设备登录码；只提示登录所需字段，不打印认证响应或令牌。
      logger.log(`[MineMate] Microsoft 认证：请在浏览器打开 ${data.verification_uri}，输入设备代码 ${data.user_code}。`);
      logger.log('[MineMate] 请登录 Bot 专用账号；不要向他人分享设备代码。等待浏览器授权完成...');
    };
  }
  return options;
}

module.exports = { createAuthOptions };
