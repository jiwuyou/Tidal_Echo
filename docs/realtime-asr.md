# 实时语音识别链路

本文记录 Tidal Echo 接入阿里云百炼 Fun-ASR 实时语音识别的设计和关键代码位置。

## 思路

通话模式需要低延迟识别，所以优先使用百炼 Fun-ASR WebSocket 实时接口。浏览器原生 WebSocket 不能在握手阶段设置 `Authorization` 请求头，因此前端不直连百炼，而是连接 relay 后端的 `/app/asr-stream`，由后端代理到百炼。

整体链路：

1. 前端获取麦克风权限。
2. 前端把麦克风音频降采样为 16 kHz、单声道、PCM int16 小块。
3. 前端通过 `wss://<host>/relay/app/asr-stream?token=...` 推送 PCM。
4. 后端校验 relay token，连接百炼 WebSocket，并发送 `run-task`。
5. 百炼返回 `task-started` 后，后端开始转发前端 PCM。
6. 后端把百炼 `result-generated` 中的中间结果回传给前端显示字幕。
7. 后端收到 `sentence_end=true` 的最终结果后，写入消息库并转发给 Claude Code channel。
8. 通话结束或前端发送 `{ "type": "finish" }` 时，后端发送 `finish-task` 并等待 `task-finished`。

降级策略：

1. 实时 ASR 连接失败时，回退到浏览器 `SpeechRecognition`。
2. 浏览器识别不可用或长时间无结果时，回退到 `MediaRecorder` 分段录音上传。
3. 录音上传后端优先走百炼非实时 ASR，失败再走本地 Vosk。

## 关键代码

- `backend/app.py`
  - `/app/asr-stream`：relay 到百炼 Fun-ASR 的 WebSocket 代理。
  - `bailian_realtime_run_task()`：生成百炼 `run-task` 事件。
  - `bailian_realtime_finish_task()`：生成百炼 `finish-task` 事件。
  - `publish_realtime_voice_text()`：最终识别文本入库并转发给 Claude Code。
- `web/index.html`
  - `startRealtimeAsr()`：通话模式实时 ASR 入口。
  - `downsampleTo16k()` / `pcm16Buffer()`：浏览器侧音频格式转换。
  - `stopRealtimeAsr()`：结束通话或切换播放时关闭实时识别。
- `scripts/openhouse_local_gateway.py`
  - 本地 `/relay` 网关。
  - `_proxy_websocket()` 支持 WebSocket Upgrade 透传，否则手机 HTTPS 页面无法连接 `/relay/app/asr-stream`。
- `scripts/bailian_asr_transcribe.py`
  - 录音上传的百炼非实时 ASR fallback。
- `scripts/vosk_transcribe.py`
  - 本地离线 ASR fallback。

## 配置

示例变量在 `backend/.env.example`：

```env
BAILIAN_API_KEY=
BAILIAN_WORKSPACE_ID=
BAILIAN_REALTIME_ASR_WS_ENDPOINT=
BAILIAN_REALTIME_ASR_MODEL=fun-asr-realtime
BAILIAN_REALTIME_ASR_SAMPLE_RATE=16000
BAILIAN_REALTIME_ASR_LANGUAGE=zh
BAILIAN_REALTIME_ASR_MAX_SILENCE=800
```

如果没有 `BAILIAN_WORKSPACE_ID`，可以用兼容域名：

```env
BAILIAN_REALTIME_ASR_WS_ENDPOINT=wss://dashscope.aliyuncs.com/api-ws/v1/inference
```

## 验证

基础检查：

```bash
python -m py_compile backend/app.py scripts/*.py
node --check /tmp/tidal-echo-inline.js
curl http://127.0.0.1:23087/relay/healthz
```

端到端验证点：

1. `/relay/app/asr-stream` 能收到 `started`。
2. 真实 PCM 音频能收到 `asr final=true`。
3. 后端返回 `committed`，消息库出现 `source=bailian_realtime` 的 voice 消息。
4. `plugin_subs=1` 时 Claude Code 能收到该消息并回复。
