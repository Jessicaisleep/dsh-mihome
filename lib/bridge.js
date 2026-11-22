/**
 * MiHome 桥接进程的 Node 侧客户端。
 *
 * Python 侧（python/mihome_bridge.py）是常驻子进程，协议是 JSON Lines：
 * 往 stdin 写一行请求、从 stdout 读一行响应。一行一问一答，请求自带 id，
 * 这里用 id 把响应配回各自的 Promise。
 *
 * 为什么常驻而不是每次 spawn：MijiaService 构造时会读本地凭据、建
 * requests.Session、加载设备缓存，冷启动几百毫秒到数秒；而一次会话里
 * 用户往往连打好几个米家请求。常驻进程让第二次起几乎零成本。
 *
 * stdout 是协议通道，Python 侧的任何 print 都会污染它——所以那边的日志
 * 全部走 stderr，这里只把 stderr 收进环形缓冲，出错时附进报错里。
 *
 * @module dsh-mihome/lib/bridge
 */

import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { StringDecoder } from "node:string_decoder";

/** 桥接进程异常退出时保留的最后若干行 stderr，用于把真实原因带回给调用方。 */
const STDERR_KEEP = 40;

/**
 * 一个桥接进程 + 它的请求队列。
 *
 * 生命周期由调用方（插件 apply）持有：插件卸载时调 dispose() 收尾。
 * 进程意外退出不会让对象失效——下次 request() 会自动重启，
 * 因为用户可能只是临时杀掉了进程或依赖装到一半。
 */
export class Bridge {
  /**
   * @param {{ pythonExe: string, script: string, dataDir: string, cwd?: string, env?: Record<string,string> }} options
   */
  constructor(options) {
    this.pythonExe = options.pythonExe;
    this.script = options.script;
    this.dataDir = options.dataDir;
    this.cwd = options.cwd;
    this.extraEnv = options.env ?? {};

    this.child = null;
    this.pending = new Map();
    this.nextId = 1;
    this.childStates = new WeakMap();
    this.disposed = false;
  }

  /** 依赖是否就绪——给 status 工具做体检用，不真的启动进程。 */
  check() {
    return {
      pythonExe: this.pythonExe,
      pythonExists: existsSync(this.pythonExe),
      script: this.script,
      scriptExists: existsSync(this.script),
      dataDir: this.dataDir,
      running: this.child !== null && this.child.exitCode === null,
    };
  }

  #ensureChild() {
    if (this.disposed) throw new Error("MiHome 桥接已关闭");
    if (this.child && this.child.exitCode === null && this.child.signalCode === null) return this.child;
    // The exited process may still drain stdout; its pending IDs and timers remain owned by it.
    if (this.child) this.child = null;

    if (!existsSync(this.pythonExe)) {
      throw new Error(
        `找不到米家桥接用的 Python：${this.pythonExe}\n` +
        "插件首次使用需要在 <插件目录> 建 .venv 并安装 mijiaAPI（见 README）。",
      );
    }
    if (!existsSync(this.script)) {
      throw new Error(`找不到桥接脚本：${this.script}`);
    }

    // windowsHide：这个子进程是后台协议通道，不该在桌面弹出控制台窗口
    const child = spawn(this.pythonExe, [this.script], {
      cwd: this.cwd,
      windowsHide: true,
      stdio: ["pipe", "pipe", "pipe"],
      env: {
        ...process.env,
        // Python 默认按 locale 决定 stdout 编码，中文会变成乱码或直接抛
        // UnicodeEncodeError；强制 UTF-8 与 Node 侧解码保持一致
        PYTHONIOENCODING: "utf-8",
        PYTHONUTF8: "1",
        MIHOME_DATA_DIR: this.dataDir,
        ...this.extraEnv,
      },
    });

    const state = {
      stdoutDecoder: new StringDecoder("utf8"),
      stderrDecoder: new StringDecoder("utf8"),
      stdoutBuffer: "",
      stderrBuffer: "",
      stderrTail: [],
    };
    this.childStates.set(child, state);
    child.stdout.on("data", (chunk) => this.#onStdout(child, chunk));
    child.stderr.on("data", (chunk) => this.#onStderr(child, chunk));
    child.stdin.on("error", (error) => {
      if (this.child === child) this.#terminateForRequest(new Error(`桥接输入通道失败：${error.message}`));
    });
    child.on("error", (error) => {
      if (this.child === child) this.child = null;
      this.#failForChild(child, new Error(`米家桥接进程启动失败：${error.message}`));
    });

    // exit can precede the last stdout data event. Keep draining the current
    // child until close; #ensureChild retires its requests before replacement.
    child.on("close", (code, signal) => {
      const state = this.childStates.get(child);
      const tail = state?.stderrTail.join("\n").trim() ?? "";
      if (this.child === child) this.child = null;
      const why = signal ? `信号 ${signal}` : `退出码 ${code}`;
      this.#failForChild(child, new Error(
        `米家桥接进程已退出（${why}）。` + (tail ? `\n最近的 stderr：\n${tail}` : ""),
      ));
      this.childStates.delete(child);
    });

    this.child = child;
    return child;
  }

  #onStdout(child, chunk) {
    const state = this.childStates.get(child);
    if (!state) return;
    state.stdoutBuffer += state.stdoutDecoder.write(chunk);
    // 一行一个响应；最后一段可能不完整，留在该进程的缓冲里等下一块。
    let index;
    while ((index = state.stdoutBuffer.indexOf("\n")) >= 0) {
      const line = state.stdoutBuffer.slice(0, index).trim();
      state.stdoutBuffer = state.stdoutBuffer.slice(index + 1);
      if (!line) continue;
      this.#onLine(child, line);
    }
  }

  #onStderr(child, chunk) {
    const state = this.childStates.get(child);
    if (!state) return;
    state.stderrBuffer += state.stderrDecoder.write(chunk);
    let index;
    while ((index = state.stderrBuffer.indexOf("\n")) >= 0) {
      const line = state.stderrBuffer.slice(0, index);
      state.stderrBuffer = state.stderrBuffer.slice(index + 1);
      if (!line.trim()) continue;
      state.stderrTail.push(line);
      if (state.stderrTail.length > STDERR_KEEP) state.stderrTail.shift();
    }
  }

  #onLine(child, line) {
    let message;
    try {
      message = JSON.parse(line);
    } catch {
      // 非协议 stdout 也要留在产生它的进程诊断上下文中。
      const tail = this.childStates.get(child)?.stderrTail;
      if (tail) {
        tail.push(`[stdout] ${line}`);
        if (tail.length > STDERR_KEEP) tail.shift();
      }
      return;
    }
    const entry = this.pending.get(message.id);
    if (!entry) return;
    if (message.ok === false && message.code === "operation_timeout") {
      const error = new Error("操作超时，执行结果未知；桥接已终止，请先读取设备状态，再决定是否重试。");
      error.code = "operation_timeout";
      error.outcomeUnknown = true;
      this.#terminateForRequest(error, message.id);
      return;
    }
    this.pending.delete(message.id);
    this.#cleanupEntry(entry);
    if (message.ok) {
      entry.resolve(message.result ?? {});
      return;
    }
    const error = new Error(message.error || "米家桥接返回了失败响应");
    error.code = message.code ?? "error";
    entry.reject(error);
  }

  #cleanupEntry(entry) {
    clearTimeout(entry.timer);
    if (entry.signal && entry.abortHandler) {
      entry.signal.removeEventListener("abort", entry.abortHandler);
    }
  }

  #failAll(error) {
    for (const [, entry] of this.pending) {
      this.#cleanupEntry(entry);
      entry.reject(error);
    }
    this.pending.clear();
  }

  #failForChild(child, error) {
    for (const [id, entry] of this.pending) {
      if (entry.child !== child) continue;
      this.pending.delete(id);
      this.#cleanupEntry(entry);
      entry.reject(error);
    }
  }

  #terminateForRequest(error, requestId, ownerChild) {
    const entry = requestId === undefined ? null : this.pending.get(requestId);
    const child = ownerChild ?? entry?.child ?? this.child;
    if (this.child === child) this.child = null;
    if (requestId !== undefined) {
      if (entry) {
        this.pending.delete(requestId);
        this.#cleanupEntry(entry);
        entry.reject(error);
      }
      const interrupted = new Error("共享桥接已中断；其他在途请求的执行结果未知，请读取设备状态后再决定是否重试。");
      interrupted.code = "bridge_interrupted";
      if (child) this.#failForChild(child, interrupted);
    } else if (child) {
      this.#failForChild(child, error);
    } else {
      this.#failAll(error);
    }
    if (child && child.exitCode === null) {
      try { child.kill(); } catch { /* already exited */ }
    }
  }

  /**
   * 调用一个 op，等到对应的响应行。
   *
   * @param {string} op
   * @param {Record<string, unknown>} [args]
   * @param {number} [timeoutMs] 单次请求超时；登录长轮询要单独给大值
   * @param {AbortSignal} [signal] 取消时终止当前桥接进程
   * @returns {Promise<any>}
   */
  request(op, args = {}, timeoutMs = 30_000, signal) {
    if (timeoutMs && typeof timeoutMs === "object") {
      signal = timeoutMs.signal;
      timeoutMs = timeoutMs.timeoutMs ?? 30_000;
    }
    if (signal?.aborted) {
      const error = new Error("请求已取消");
      error.name = "AbortError";
      return Promise.reject(error);
    }
    const child = this.#ensureChild();
    const id = this.nextId++;
    const payload = JSON.stringify({ id, op, args }) + "\n";

    return new Promise((resolve, reject) => {
      const entry = { resolve, reject, timer: null, signal, abortHandler: null, child };
      const abortError = () => {
        const error = new Error(`米家桥接请求已取消（${op}）。`);
        error.name = "AbortError";
        this.#terminateForRequest(error, id, child);
      };
      entry.abortHandler = abortError;
      entry.timer = setTimeout(() => {
        this.#terminateForRequest(new Error(
          `米家桥接请求超时（${op}，${timeoutMs}ms）。` +
          (this.childStates.get(child)?.stderrTail.length ? `\n最近的 stderr：\n${this.childStates.get(child).stderrTail.slice(-8).join("\n")}` : ""),
        ), id, child);
      }, timeoutMs);
      if (typeof entry.timer.unref === "function") entry.timer.unref();
      signal?.addEventListener("abort", abortError, { once: true });
      this.pending.set(id, entry);
      try {
        child.stdin.write(payload, "utf8");
      } catch (error) {
        this.pending.delete(id);
        this.#cleanupEntry(entry);
        reject(new Error(`写入米家桥接进程失败：${error.message}`));
      }
    });
  }

  /** 关掉子进程并让所有在途请求失败。插件卸载时调用。 */
  dispose() {
    this.disposed = true;
    this.#failAll(new Error("MiHome 桥接已关闭"));
    if (this.child && this.child.exitCode === null) {
      try {
        this.child.stdin.end();
        this.child.kill();
      } catch {
        // 已经退出的进程再 kill 会抛，忽略即可
      }
    }
    this.child = null;
  }
}
