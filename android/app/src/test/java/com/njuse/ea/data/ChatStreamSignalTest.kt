package com.njuse.ea.data

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.ServerSocket
import java.util.concurrent.atomic.AtomicInteger

/**
 * SSE 事件解析与时序的纯 JVM 测试（本地假服务，不碰真后端）。
 *
 * 针对的缺陷：主界面已经显示完整回复，发送键却长时间不可点。原因是客户端把
 * 「连接结束」当成了「本轮回答结束」——而按后端契约，`final` 排在决策线与沉淀线
 * （画像/摘要，最慢数十秒）之后才发，连接因此迟迟不关；本轮回答真正结束的信号
 * 是更早到达的 `dialogue_done`（后端在决策/沉淀之前就发，注释见 scheduler）。
 *
 * 这里把这两件事的**先后关系**钉死：
 *  - `onDialogueDone` 必须在连接仍然打开时就被回调（本测试在事件后故意静默 1.5s）；
 *  - 没有 `dialogue_done` 的流不回调（老后端 / 异常流不误触发）；
 *  - 重复的 `dialogue_done` 只回调一次；
 *  - 文本与风险标志照旧解析（不带回归）。
 */
class ChatStreamSignalTest {

    /** 极简假 SSE 服务：按脚本逐段写出响应体，每段之后可等待，末尾关闭连接。 */
    private class FakeSseServer(private val chunks: List<Pair<String, Long>>) {

        private val server = ServerSocket(0)
        private val thread = Thread {
            try {
                server.accept().use { socket ->
                    val reader = BufferedReader(InputStreamReader(socket.getInputStream()))
                    var contentLength = 0
                    while (true) {
                        val line = reader.readLine() ?: break
                        if (line.isEmpty()) break
                        if (line.startsWith("Content-Length:", ignoreCase = true)) {
                            contentLength = line.substringAfter(":").trim().toIntOrNull() ?: 0
                        }
                    }
                    repeat(contentLength) { reader.read() }   // 吃掉请求体

                    val out = socket.getOutputStream()
                    out.write(
                        (
                            "HTTP/1.1 200 OK\r\n" +
                                "Content-Type: text/event-stream\r\n" +
                                "Cache-Control: no-cache\r\n" +
                                "Connection: close\r\n\r\n"
                            ).toByteArray()
                    )
                    out.flush()
                    for ((chunk, waitMs) in chunks) {
                        out.write(chunk.toByteArray())
                        out.flush()
                        if (waitMs > 0) Thread.sleep(waitMs)
                    }
                }
            } catch (_: Throwable) {
                // 测试结束时主动关 socket 会让 accept/read 抛异常，忽略即可
            }
        }

        fun start() {
            thread.isDaemon = true
            thread.start()
        }

        fun stop() {
            runCatching { server.close() }
            thread.join(5_000)
        }

        val baseUrl: String get() = "http://127.0.0.1:${server.localPort}/"
    }

    @Test(timeout = 60_000)
    fun streamChat_dialogueDone_firesWhileConnectionStillOpen() = runBlocking {
        // 真实时序：token 流完 → dialogue_done → 后端还在跑决策/沉淀（这里静默 1.5s）→ final → 关闭
        val server = FakeSseServer(
            listOf(
                "event: token\ndata: \"你好\"\n\n" to 0L,
                "event: dialogue_done\ndata: {}\n\n" to 1_500L,
                "event: final\ndata: {\"risk_state\":\"none\",\"crisis\":false}\n\n" to 0L,
            )
        )
        server.start()
        try {
            val doneCount = AtomicInteger(0)
            var doneAt = 0L
            var returnedAt = 0L

            val result = ChatRepository(server.baseUrl).streamChat(
                userId = "u1",
                message = "hi",
                onToken = {},
                onDialogueDone = {
                    doneAt = System.currentTimeMillis()
                    doneCount.incrementAndGet()
                }
            )
            returnedAt = System.currentTimeMillis()

            assertTrue("streamChat 应成功返回", result.isSuccess)
            val stream = result.getOrThrow()
            assertEquals("你好", stream.text)
            assertEquals("none", stream.riskState)
            assertFalse(stream.crisis)

            assertEquals("dialogue_done 只回调一次", 1, doneCount.get())
            assertTrue("dialogue_done 必须早于连接结束被回调", doneAt in 1..returnedAt)
            // 服务端在事件后刻意静默 1.5s，回调必须远早于返回（留 1s 余量容忍调度抖动）
            assertTrue(
                "回调应比连接关闭早至少 1s（实际 ${returnedAt - doneAt}ms）",
                returnedAt - doneAt >= 1_000
            )
        } finally {
            server.stop()
        }
    }

    @Test(timeout = 60_000)
    fun streamChat_withoutDialogueDone_doesNotSignal() = runBlocking {
        // 老后端 / 异常路径：没有 dialogue_done，解析与返回照旧，只是不发信号
        val server = FakeSseServer(
            listOf(
                "event: token\ndata: \"喵\"\n\n" to 0L,
                "event: final\ndata: {\"risk_state\":\"crisis\",\"crisis\":true}\n\n" to 0L,
            )
        )
        server.start()
        try {
            val doneCount = AtomicInteger(0)
            val result = ChatRepository(server.baseUrl).streamChat(
                userId = "u1",
                message = "hi",
                onToken = {},
                onDialogueDone = { doneCount.incrementAndGet() }
            )

            assertTrue(result.isSuccess)
            assertEquals("喵", result.getOrThrow().text)
            assertEquals("crisis", result.getOrThrow().riskState)
            assertTrue(result.getOrThrow().crisis)
            assertEquals("没有 dialogue_done 就不该回调", 0, doneCount.get())
        } finally {
            server.stop()
        }
    }

    @Test(timeout = 60_000)
    fun streamChat_repeatedDialogueDone_signalsOnce() = runBlocking {
        val server = FakeSseServer(
            listOf(
                "event: dialogue_done\ndata: {}\n\n" to 0L,
                "event: dialogue_done\ndata: {}\n\n" to 0L,
                "event: token\ndata: \"a\"\n\n" to 0L,
            )
        )
        server.start()
        try {
            val doneCount = AtomicInteger(0)
            val result = ChatRepository(server.baseUrl).streamChat(
                userId = "u1",
                message = "hi",
                onToken = {},
                onDialogueDone = { doneCount.incrementAndGet() }
            )

            assertTrue(result.isSuccess)
            assertEquals("a", result.getOrThrow().text)
            assertEquals("重复事件只回调一次", 1, doneCount.get())
        } finally {
            server.stop()
        }
    }
}
