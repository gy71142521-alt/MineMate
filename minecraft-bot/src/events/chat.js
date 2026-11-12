function registerChatEvents(bot, logger = console, movement, perception, agent) {
  let ready = false;
  let generation = 0;

  function send(message) {
    try {
      bot.chat(message);
      logger.log('[MineMate] 已发送回复');
      return true;
    } catch {
      logger.error('[MineMate] 聊天发送失败，请检查连接状态。');
      return false;
    }
  }

  function getSnapshot() {
    try {
      return perception?.getWorldSnapshot() ?? null;
    } catch {
      logger.error('[Perception] 获取状态快照失败。');
      return null;
    }
  }

  function unavailable() {
    send('游戏状态尚未就绪，数据暂不可用，请确认 Bot 已进入世界后再试。');
  }

  // 按实际长度截断，避免长实体名或负坐标使消息超过游戏聊天限制。
  function sendSummary(prefix, entries, truncated = false) {
    const suffix = '；结果已截断';
    let message = prefix;
    let shown = 0;
    for (const entry of entries) {
      const next = `${shown ? '；' : ''}${entry}`;
      if (message.length + next.length + suffix.length > 240) break;
      message += next;
      shown += 1;
    }
    if (truncated || shown < entries.length) message += suffix;
    send(message);
  }

  function respondWithStatus() {
    const snapshot = getSnapshot();
    const player = snapshot?.player;
    if (snapshot?.availability?.player !== 'available' ||
      !player || ![player.health, player.food, player.position?.x, player.position?.y, player.position?.z].every(Number.isFinite)) {
      unavailable();
      return;
    }
    const dimension = player.dimension ?? '未知';
    const level = Number.isFinite(player.experienceLevel) ? player.experienceLevel : '不可用';
    const alive = player.alive === true ? '存活' : player.alive === false ? '死亡' : '不可用';
    send(`生命值：${player.health}/20，饥饿值：${player.food}/20，位置：X=${player.position.x.toFixed(2)} Y=${player.position.y.toFixed(2)} Z=${player.position.z.toFixed(2)}，维度：${dimension}，经验等级：${level}，${alive}`);
  }

  function respondWithInventory() {
    const snapshot = getSnapshot();
    if (snapshot?.availability?.inventory !== 'available' || !Array.isArray(snapshot.inventory)) {
      unavailable();
      return;
    }
    if (snapshot.inventory.length === 0) {
      send('背包为空。');
      return;
    }
    const totals = new Map();
    for (const item of snapshot.inventory) totals.set(item.name, (totals.get(item.name) ?? 0) + item.count);
    const entries = [...totals].map(([name, count]) => `${name}×${count}`);
    sendSummary('背包：', entries.slice(0, 7), entries.length > 7);
  }

  function respondWithNearbyEntities() {
    const snapshot = getSnapshot();
    if (snapshot?.availability?.nearbyEntities !== 'available' || !Array.isArray(snapshot.nearbyEntities)) {
      unavailable();
      return;
    }
    if (snapshot.nearbyEntities.length === 0) {
      send('附近没有已加载的实体。');
      return;
    }
    const labels = { player: '玩家', passive: '被动生物', hostile: '敌对生物', other: '其他实体' };
    const shown = snapshot.nearbyEntities.slice(0, 5);
    const entries = shown.map((entity) => `${entity.name}（${labels[entity.category] ?? labels.other}，${entity.distance.toFixed(1)}格）`);
    sendSummary('附近实体：', entries, snapshot.nearbyEntities.length > shown.length || snapshot.truncated?.entities);
  }

  function respondWithNearbyBlocks() {
    const snapshot = getSnapshot();
    if (snapshot?.availability?.nearbyBlocks !== 'available' || !Array.isArray(snapshot.nearbyBlocks)) {
      unavailable();
      return;
    }
    if (snapshot.nearbyBlocks.length === 0) {
      send('附近没有已加载的常见方块或资源。');
      return;
    }
    const shown = snapshot.nearbyBlocks.slice(0, 6);
    const entries = shown.map((block) => `${block.name} [${block.position.x}, ${block.position.y}, ${block.position.z}]（${block.distance.toFixed(1)}格）`);
    sendSummary('附近方块：', entries, snapshot.nearbyBlocks.length > shown.length || snapshot.truncated?.blocks);
  }

  // spawn 也会在重生时触发，欢迎语只发送一次。
  bot.once('spawn', () => {
    ready = true;
    send('大家好，我是 MineMate！发送“你好”或“状态”可以和我互动。');
  });
  bot.on('end', () => { ready = false; generation += 1; });
  bot.on('kicked', () => { ready = false; generation += 1; });
  // 丢弃死亡或换维度前仍在云端处理的旧回复。
  bot.on('death', () => { generation += 1; });
  bot.on('respawn', () => { generation += 1; });

  // 异步到达/失败统一反馈，监听器在注册聊天时添加一次，不随指令累积。
  function onMovementResult(result) {
    if (ready) send(result.message);
  }
  movement?.on('result', onMovementResult);
  bot.once('end', () => movement?.removeListener('result', onMovementResult));

  bot.on('chat', (username, message) => {
    if (username === bot.username) return;
    const logMessage = message.replace(/\bsk-[A-Za-z0-9_-]+/gu, '[已隐藏]')
      .replace(/((?:api[_-]?key|access[_-]?token|password)\s*[:=]\s*)\S+/giu, '$1[已隐藏]')
      .replace(/[\u0000-\u001f\u007f]/gu, ' ').slice(0, 500);
    logger.log(`[Chat] ${username}: ${logMessage}`);
    if (!ready) return;

    const command = message.trim();
    if (command === '你好') {
      send(`你好，${username}！很高兴和你一起玩 Minecraft。`);
    } else if (command === '状态') {
      respondWithStatus();
    } else if (command === '背包') {
      respondWithInventory();
    } else if (command === '附近') {
      respondWithNearbyEntities();
    } else if (command === '环境') {
      respondWithNearbyBlocks();
    } else if (command === '停止' || command === '跟随我' || /^前往(?:\s|$)/u.test(command)) {
      if (!movement) {
        send('移动系统尚未就绪。');
        return;
      }
      try {
        if (command === '停止') generation += 1;
        agent?.cancel?.(username);
        let result;
        if (command === '停止') {
          result = movement.stop(username);
        } else if (command === '跟随我') {
          result = movement.follow(username);
        } else {
          const parts = command.split(/\s+/u);
          if (parts.length !== 4) {
            send('用法：前往 X Y Z，例如：前往 100 65 200。');
            return;
          }
          result = movement.goTo(username, ...parts.slice(1).map(Number));
        }
        if (result?.ok && command !== '停止') generation += 1;
        send(result.message);
      } catch {
        // 意外错误也先释放当前任务，避免异常后仍继续主动移动。
        try { movement.stop(username); } catch { logger.error('[Movement] 异常后取消任务失败。'); }
        logger.error('[Movement] 处理移动指令失败。');
        send('处理移动指令失败，请检查终端日志。');
      }
    } else if (agent && command) {
      const currentGeneration = generation;
      const toolGeneration = bot.tools?.getState().generation;
      const requestArguments = Number.isInteger(toolGeneration) ? [username, command, { generation: toolGeneration }] : [username, command];
      // 不等待网络；旧指令不会进入此分支，返回 intent 也不会传给移动模块。
      Promise.resolve().then(() => ready && currentGeneration === generation ? agent.message(...requestArguments) : null).then((result) => {
        if (ready && currentGeneration === generation && result?.reply) send(result.reply);
      }).catch(() => {
        logger.error('[Agent] 聊天处理失败。');
        if (ready && currentGeneration === generation) send('自然语言服务暂不可用；旧指令仍可使用。');
      });
    }
  });
}

module.exports = { registerChatEvents };
