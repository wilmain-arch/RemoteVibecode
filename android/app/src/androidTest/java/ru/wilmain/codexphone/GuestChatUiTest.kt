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
        compose.onAllNodesWithText("Сообщение в общей ленте").assertCountEquals(1)
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
