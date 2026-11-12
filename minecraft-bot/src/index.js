const { loadConfig } = require('./config');
const { startBot } = require('./bot');
const { startApiServer } = require('./httpServer');

let apiServer;
try {
  const config = loadConfig();
  const bot = startBot(config, {
    onFailure: () => {
      process.exitCode = 1;
      apiServer?.close();
    }
  });
  if (bot) apiServer = startApiServer({
    bot,
    movement: bot.movement,
    perception: bot.perception,
    host: config.http.host,
    port: config.http.port,
    maxBodyBytes: config.http.maxBodyBytes,
    onError: () => { process.exitCode = 1; }
  });
} catch (error) {
  // 此处配置错误均由本模块生成，不打印环境变量或完整异常对象。
  console.error(`[MineMate] 启动失败：${error.message}`);
  process.exitCode = 1;
}
