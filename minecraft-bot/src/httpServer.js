const http = require('node:http');

const DEFAULT_MAX_BODY_BYTES = 16 * 1024;

function envelope(success, data = null, error = null) {
  return { success, data, error };
}

function sendJson(response, statusCode, body) {
  const payload = JSON.stringify(body);
  response.statusCode = statusCode;
  response.setHeader('Content-Type', 'application/json; charset=utf-8');
  response.setHeader('Content-Length', Buffer.byteLength(payload));
  response.end(payload);
}

function errorBody(code, message) {
  return envelope(false, null, { code, message });
}

function validUsername(value) {
  return typeof value === 'string' && /^[A-Za-z0-9_]{3,16}$/.test(value);
}

function hasOnlyKeys(value, keys) {
  return value && typeof value === 'object' && !Array.isArray(value) &&
    Object.keys(value).every((key) => keys.includes(key));
}

function validatePayload(pathname, body) {
  if (pathname.endsWith('/follow') || pathname.endsWith('/stop')) {
    if (!hasOnlyKeys(body, ['username']) || !validUsername(body.username)) {
      return 'username 必须是 3–16 位英文字母、数字或下划线组成的游戏昵称。';
    }
    return null;
  }
  if (pathname.endsWith('/goto')) {
    if (!hasOnlyKeys(body, ['username', 'x', 'y', 'z']) || !validUsername(body.username)) {
      return '请求必须包含合法的 username、x、y、z，且不能包含其他字段。';
    }
    if (![body.x, body.y, body.z].every((value) => typeof value === 'number' && Number.isFinite(value))) {
      return 'x、y、z 必须是有限数字。';
    }
  }
  return null;
}

function readJson(request, maxBytes) {
  return new Promise((resolve, reject) => {
    let size = 0;
    let raw = '';
    let rejected = false;
    request.setEncoding('utf8');
    request.on('data', (chunk) => {
      if (rejected) return;
      size += Buffer.byteLength(chunk);
      if (size > maxBytes) {
        rejected = true;
        reject(Object.assign(new Error('request body too large'), { code: 'BODY_TOO_LARGE' }));
        request.resume();
        return;
      }
      raw += chunk;
    });
    request.on('end', () => {
      if (rejected) return;
      if (!raw.trim()) return reject(Object.assign(new Error('request body is required'), { code: 'INVALID_JSON' }));
      try {
        resolve(JSON.parse(raw));
      } catch {
        reject(Object.assign(new Error('invalid json'), { code: 'INVALID_JSON' }));
      }
    });
    request.on('error', reject);
  });
}

function botStatus(bot, getStatus) {
  try {
    if (typeof getStatus === 'function') return getStatus();
    if (typeof bot?.mineMateStatus === 'function') return bot.mineMateStatus();
  } catch {
    // 状态读取失败时使用安全的不可用状态。
  }
  return { connected: false, spawned: false, state: 'unavailable' };
}

function isBotReady(bot, status) {
  return Boolean(status?.connected && status?.spawned && bot?.entity && bot.isAlive !== false);
}

function createApiServer({ bot, movement, perception, tools = bot?.tools, getStatus, logger = console, maxBodyBytes = DEFAULT_MAX_BODY_BYTES } = {}) {
  return http.createServer(async (request, response) => {
    const method = request.method ?? 'GET';
    const url = new URL(request.url ?? '/', 'http://localhost');
    const status = botStatus(bot, getStatus);
    try {
      if (method === 'GET' && url.pathname === '/health') {
        sendJson(response, 200, envelope(true, {
          service: 'ok',
          bot: { ...status, ready: isBotReady(bot, status) }
        }));
        return;
      }
      if (method === 'GET' && url.pathname === '/api/world') {
        if (!perception || typeof perception.getWorldSnapshot !== 'function') {
          sendJson(response, 503, errorBody('PERCEPTION_UNAVAILABLE', '感知模块暂不可用。'));
          return;
        }
        const snapshot = perception.getWorldSnapshot();
        if (!isBotReady(bot, status)) {
          sendJson(response, 503, envelope(false, snapshot, { code: 'BOT_NOT_READY', message: 'Bot 尚未进入游戏世界。' }));
          return;
        }
        sendJson(response, 200, envelope(true, snapshot));
        return;
      }

      if (method === 'GET' && url.pathname === '/api/tools') {
        sendJson(response, tools ? 200 : 503, tools ? envelope(true, { tools: tools.list(), generation: tools.getState().generation }) : errorBody('TOOLS_UNAVAILABLE', '工具系统尚未就绪。'));
        return;
      }
      if (method === 'POST' && ['/api/tools/execute', '/api/tools/cancel'].includes(url.pathname)) {
        if (!tools) { sendJson(response, 503, errorBody('TOOLS_UNAVAILABLE', '工具系统尚未就绪。')); return; }
        if (!/^application\/json(?:\s*;|$)/i.test(request.headers['content-type'] ?? '')) {
          sendJson(response, 415, errorBody('UNSUPPORTED_MEDIA_TYPE', '请求必须使用 application/json。')); return;
        }
        let body;
        try { body = await readJson(request, maxBodyBytes); }
        catch (error) { sendJson(response, error.code === 'BODY_TOO_LARGE' ? 413 : 400, errorBody(error.code === 'BODY_TOO_LARGE' ? 'BODY_TOO_LARGE' : 'INVALID_JSON', '请求体过大或 JSON 无效。')); return; }
        const cancelling = url.pathname.endsWith('/cancel');
        if (!hasOnlyKeys(body, cancelling ? ['username', 'requestId'] : ['username', 'tool', 'arguments', 'requestId', 'generation']) || !validUsername(body.username) ||
          (cancelling && body.requestId !== undefined && (typeof body.requestId !== 'string' || !/^[A-Za-z0-9_-]{1,80}$/.test(body.requestId))) ||
          (!cancelling && (typeof body.tool !== 'string' || !/^[a-z][a-z0-9_]{0,63}$/.test(body.tool) || typeof body.requestId !== 'string' || !/^[A-Za-z0-9_-]{1,80}$/.test(body.requestId) || !Number.isInteger(body.generation)))) {
          sendJson(response, 400, errorBody('INVALID_ARGUMENT', '工具请求格式无效。')); return;
        }
        if (cancelling) {
          const ok = tools.cancel(body.username, false, body.requestId);
          if (ok && !body.requestId) movement?.stop(body.username);
          sendJson(response, ok ? 200 : 403, ok ? envelope(true, { status: 'cancelled' }) : errorBody('FORBIDDEN', '无权取消操作。'));
          return;
        }
        if (body.generation !== tools.getState().generation) {
          sendJson(response, 409, errorBody('STALE_TASK', '任务已被停止或生命周期变化取消。')); return;
        }
        // 工具失败属于真实执行结果，保持 HTTP 200 和完整工具信封，供模型读取。
        const result = await tools.execute(body.tool, body.arguments, body.username, body.requestId);
        sendJson(response, 200, envelope(true, result));
        return;
      }

      const movementPath = method === 'POST' && ['/api/movement/follow', '/api/movement/goto', '/api/movement/stop'].includes(url.pathname);
      if (movementPath) {
        if (!isBotReady(bot, status) || !movement) {
          sendJson(response, 503, errorBody('BOT_NOT_READY', 'Bot 尚未进入游戏世界，暂不能执行移动。'));
          return;
        }
        if (!/^application\/json(?:\s*;|$)/i.test(request.headers['content-type'] ?? '')) {
          sendJson(response, 415, errorBody('UNSUPPORTED_MEDIA_TYPE', '请求必须使用 application/json。'));
          return;
        }
        let body;
        try {
          body = await readJson(request, maxBodyBytes);
        } catch (error) {
          const tooLarge = error.code === 'BODY_TOO_LARGE';
          sendJson(response, tooLarge ? 413 : 400, errorBody(tooLarge ? 'BODY_TOO_LARGE' : 'INVALID_JSON', tooLarge ? '请求体过大。' : '请求体必须是有效 JSON。'));
          return;
        }
        const validationError = validatePayload(url.pathname, body);
        if (validationError) {
          sendJson(response, 400, errorBody('INVALID_ARGUMENT', validationError));
          return;
        }
        let result;
        try {
          if (url.pathname.endsWith('/follow')) result = movement.follow(body.username);
          else if (url.pathname.endsWith('/goto')) result = movement.goTo(body.username, body.x, body.y, body.z);
          else result = movement.stop(body.username);
        } catch {
          logger.error('[HTTP] 移动接口执行失败。');
          sendJson(response, 500, errorBody('MOVEMENT_FAILED', '移动任务执行失败。'));
          return;
        }
        if (!result || result.ok === false) {
          sendJson(response, 409, envelope(false, result ?? null, { code: result?.status ?? 'MOVEMENT_REJECTED', message: result?.message ?? '移动任务被拒绝。' }));
          return;
        }
        sendJson(response, 200, envelope(true, result));
        return;
      }

      sendJson(response, 404, errorBody('NOT_FOUND', '接口不存在。'));
    } catch {
      logger.error('[HTTP] 请求处理失败。');
      if (!response.headersSent) sendJson(response, 500, errorBody('INTERNAL_ERROR', '请求处理失败。'));
    }
  });
}

function startApiServer(options = {}) {
  const server = createApiServer(options);
  const host = options.host ?? '127.0.0.1';
  const port = options.port ?? 3000;
  const logger = options.logger ?? console;
  server.on('error', (error) => {
    const message = error?.code === 'EADDRINUSE'
      ? `HTTP 端口 ${port} 已被占用，请修改 HTTP_PORT。`
      : 'HTTP API 服务启动失败，请检查 HTTP_HOST 和 HTTP_PORT。';
    logger.error(`[HTTP] ${message}`);
    options.onError?.(error);
  });
  server.listen(port, host, () => {
    const address = server.address();
    const actualPort = typeof address === 'object' && address ? address.port : port;
    logger.log(`[HTTP] API 服务已启动：http://${host}:${actualPort}`);
  });
  return server;
}

module.exports = { createApiServer, startApiServer, envelope, validatePayload, DEFAULT_MAX_BODY_BYTES };
