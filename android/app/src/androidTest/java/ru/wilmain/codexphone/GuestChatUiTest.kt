package ru.wilmain.codexphone

import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.json.JSONObject
import org.json.JSONArray
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class GuestChatUiTest {
    @get:Rule val compose=createComposeRule()
    private fun result(path:String)=when(path) {
        "guest/models"->JSONObject().put("models",JSONArray())
        "guest/self"->JSONObject().put("executionAvailable",true)
        else->JSONObject().put("tasks",JSONArray())
    }
    @Test fun queuedMessageUsesMainChatAndAppearsOnce() {
        compose.setContent {MaterialTheme(colorScheme=lightPalette,typography=appTypography) {
            GuestChatScreen("Тестовый гость","","",{_,_->},{}) {path,_->
                if(path=="guest/tasks") JSONObject().put("tasks",JSONArray().put(JSONObject()
                    .put("operationId","fixture-queue-single").put("scopeId","default").put("conversationId","main")
                    .put("text","Сообщение в общей ленте").put("state","queued"))) else result(path)
            }
        }}
        compose.waitUntil(5000){compose.onAllNodesWithText("Сообщение в общей ленте").fetchSemanticsNodes().isNotEmpty()}
        // The chat title may repeat its first message; count the queue bubble itself.
        compose.onAllNodesWithTag("queued-message:fixture-queue-single").assertCountEquals(1)
        compose.onNode(hasText("Сообщение в общей ленте") and
            hasAnyAncestor(hasTestTag("queued-message:fixture-queue-single")), useUnmergedTree=true).assertExists()
        compose.onNodeWithText("В очереди").assertExists()
        compose.onNodeWithContentDescription("Проекты и чаты").assertExists()
        compose.onNodeWithContentDescription("Файлы проекта").assertExists()
        compose.onNodeWithContentDescription("Прикрепить файл").assertExists()
    }

    @Test fun draftsRemainSeparateAcrossChats() {
        var saved=""
        compose.setContent {MaterialTheme(colorScheme=lightPalette,typography=appTypography) {
            GuestChatScreen("Тестовый гость","","",{draft,_->saved=draft},{}) {path,_->result(path)}
        }}
        compose.onNodeWithText("Сообщение для Codex").performTextInput("Первый черновик")
        compose.onNodeWithContentDescription("Проекты и чаты").performClick()
        compose.onNodeWithContentDescription("Новый чат").performClick()
        compose.onNodeWithText("Сообщение для Codex").performTextInput("Второй черновик")
        compose.onNodeWithContentDescription("Проекты и чаты").performClick()
        compose.onNodeWithText("Основной чат").performScrollTo().performClick()
        compose.onNodeWithText("Первый черновик").assertExists()
        val drafts=JSONObject(saved).getJSONObject("drafts")
        assertEquals("Первый черновик",drafts.getString("default/main"))
        assertTrue(drafts.keys().asSequence().any {drafts.optString(it)=="Второй черновик"})
    }
    @Test fun pendingBubbleRemainsVisibleAfterLostResponse() {
        val pending=JSONObject().put("operationId","fixture-pending-network").put("scopeId","default")
            .put("conversationId","main").put("text","Ожидающее сообщение без ответа")
        compose.setContent {MaterialTheme(colorScheme=darkPalette,typography=appTypography) {
            GuestChatScreen("Тестовый гость",pending.getString("text"),pending.toString(),{_,_->},{}) {path,_->result(path)}
        }}
        compose.waitUntil(5000){compose.onAllNodesWithTag("queued-message:fixture-pending-network").fetchSemanticsNodes().isNotEmpty()}
        compose.onAllNodesWithTag("queued-message:fixture-pending-network").assertCountEquals(1)
    }

    @Test fun acknowledgementClearsOnlyMatchingOriginalDraft() {
        val request=JSONObject().put("scopeId","default").put("conversationId","main").put("text","Отправленное")
        val store=JSONObject().put("version",1).put("drafts",JSONObject().put("default/main","Отправленное").put("default/other","Сохранить"))
        val result=acknowledgeGuestDraft(store.toString(),"default/other","Сохранить",request)
        assertEquals("Сохранить",result.second)
        assertEquals("",JSONObject(result.first).getJSONObject("drafts").getString("default/main"))
        val changed=acknowledgeGuestDraft(store.toString(),"default/main","Новое сообщение",request)
        assertEquals("Новое сообщение",changed.second)
        assertEquals("Новое сообщение",JSONObject(changed.first).getJSONObject("drafts").getString("default/main"))
    }

    @Test fun cancelPendingMessageClearsOnlyAfterAcknowledgement() {
        val pending=JSONObject().put("operationId","fixture-cancel-pending").put("scopeId","default")
            .put("conversationId","main").put("text","Отменить без повторного запуска")
        var savedPending=pending.toString()
        val attempts=mutableListOf<String>()
        compose.setContent {MaterialTheme(colorScheme=lightPalette,typography=appTypography) {
            GuestChatScreen("Тестовый гость",pending.getString("text"),pending.toString(),{_,outbox->savedPending=outbox},{}) {path,payload->
                if(path=="guest/cancel") {attempts.add(payload!!.getString("operationId"));JSONObject().put("state","cancelled")}
                else result(path)
            }
        }}
        compose.waitUntil(5000){compose.onAllNodesWithTag("queued-message:fixture-cancel-pending").fetchSemanticsNodes().isNotEmpty()}
        compose.onNodeWithText("Отменить").performClick()
        compose.waitUntil(5000){savedPending.isBlank()}
        assertEquals(listOf("fixture-cancel-pending"),attempts)
        compose.onAllNodesWithTag("queued-message:fixture-cancel-pending").assertCountEquals(0)
        compose.onNodeWithText("Отменить без повторного запуска").assertExists()
    }

    @Test fun lateAcceptancePreservesDraftInAnotherSelectedChat() {
        val accepted=java.util.concurrent.atomic.AtomicBoolean(false)
        var savedPending="pending"
        var savedDraft=""
        val task=JSONObject().put("operationId","fixture-late-acceptance").put("scopeId","default").put("conversationId","main").put("text","Отправленное").put("state","completed")
        val store=JSONObject().put("version",1).put("drafts",JSONObject().put("default/main","Отправленное").put("default/other","Сохранить другой черновик"))
        compose.setContent {MaterialTheme(colorScheme=lightPalette,typography=appTypography) {
            GuestChatScreen("Тестовый гость",store.toString(),task.toString(),{draft,outbox->savedDraft=draft;savedPending=outbox},{}) {path,_->
                if(path=="guest/tasks"&&accepted.get())JSONObject().put("tasks",JSONArray().put(task)) else result(path)
            }
        }}
        compose.onNodeWithContentDescription("Проекты и чаты").performClick()
        compose.onNodeWithText("Черновик other").performScrollTo().performClick()
        accepted.set(true)
        compose.waitUntil(7000){savedPending.isBlank()}
        compose.onNodeWithText("Сохранить другой черновик").assertExists()
        val drafts=JSONObject(savedDraft).getJSONObject("drafts")
        assertEquals("Сохранить другой черновик",drafts.getString("default/other"))
        assertEquals("",drafts.getString("default/main"))
    }

    @Test fun taskDurationRequiresActualAcceptedTurnTimestamps() {
        val task=JSONObject().put("state","completed").put("created",1)
        assertEquals("Готово",guestTaskOutcome(task))
        task.put("startedAt",100).put("completedAt",174)
        assertEquals("Готово · 1 мин 14 с",guestTaskOutcome(task))
    }

    @Test fun lostSendResponseRetriesSameOperation() {
        val attempts=mutableListOf<JSONObject>()
        compose.setContent {MaterialTheme(colorScheme=darkPalette,typography=appTypography) {
            GuestChatScreen("Тестовый гость","","",{_,_->},{}) {path,payload->
                if(path=="guest/send") {
                    synchronized(attempts){attempts.add(JSONObject(payload!!.toString()))}
                    throw IllegalStateException("Синтетический потерянный ответ")
                }
                result(path)
            }
        }}
        compose.onNodeWithText("Сообщение для Codex").performTextInput("Синтетическая задача")
        compose.onNodeWithContentDescription("Отправить сообщение").performClick()
        compose.waitUntil(5000){synchronized(attempts){attempts.size==1}}
        compose.onNodeWithContentDescription("Отправить сообщение").performClick()
        compose.waitUntil(5000){synchronized(attempts){attempts.size==2}}
        assertEquals(attempts[0].getString("operationId"),attempts[1].getString("operationId"))
        assertEquals(attempts[0].toString(),attempts[1].toString())
    }
}
