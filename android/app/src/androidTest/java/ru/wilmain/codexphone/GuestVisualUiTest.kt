package ru.wilmain.codexphone

import android.graphics.Bitmap
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.ui.graphics.toPixelMap
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.unit.Density
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.json.JSONObject
import org.json.JSONArray
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File

@RunWith(AndroidJUnit4::class)
class GuestVisualUiTest {
    @get:Rule val compose=createComposeRule()
    private val context get()=InstrumentationRegistry.getInstrumentation().targetContext
    private fun task()=JSONObject().put("operationId","visual-task-fixture").put("scopeId","default").put("conversationId","main")
        .put("text","Проверь оформление гостевого чата").put("state","completed").put("startedAt",100).put("completedAt",174)
        .put("result",JSONObject().put("output",JSONObject().put("messages",JSONArray().put("Готово. Изменения сохранены в [demo.txt](/workspace/demo.txt).")))
            .put("measurement",JSONObject().put("usage",JSONObject().put("fiveHours",1).put("week",0.5))))
    private fun capture(name:String) {
        compose.waitForIdle()
        val image=compose.onRoot().captureToImage()
        File(context.cacheDir,"guest-audit-$name.png").outputStream().use {image.asAndroidBitmap().compress(Bitmap.CompressFormat.PNG,100,it)}
    }
    private fun chat(mode:String,font:Float=1f) {
        context.getSharedPreferences("companion",0).edit().putString("themeMode",mode).commit()
        compose.setContent {CompositionLocalProvider(LocalDensity provides Density(LocalDensity.current.density,font)) {
            GuestChatScreen("Синтетический гость","","",{_,_->},{}) {path,_->when(path) {
                "guest/self"->JSONObject().put("executionAvailable",true).put("quotas",JSONObject()
                    .put("fiveHours",JSONObject().put("rule",JSONObject().put("mode","unlimited")))
                    .put("week",JSONObject().put("rule",JSONObject().put("mode","fixed")).put("remaining",12)))
                "guest/models"->JSONObject().put("models",JSONArray())
                else->JSONObject().put("tasks",JSONArray().put(task()))
            }}
        }}
        compose.waitUntil(5000){compose.onAllNodesWithText("Готово · 1 мин 14 с",substring=true).fetchSemanticsNodes().isNotEmpty()}
    }
    @Test fun lightGuestChatHasCompletionAndReadableSurface() {
        chat("light");capture("chat-light")
        val image=compose.onRoot().captureToImage();val color=image.toPixelMap()[1,image.height/2]
        assertTrue(color.red>0.9f&&color.green>0.9f)
        compose.onNodeWithContentDescription("Файлы проекта").assertExists()
    }
    @Test fun darkGuestChatHasCompletionAndReadableSurface() {
        chat("dark");capture("chat-dark")
        val image=compose.onRoot().captureToImage();val color=image.toPixelMap()[1,image.height/2]
        assertTrue(color.red<0.25f&&color.green<0.25f)
    }
    @Test fun guestChatAt200PercentFontKeepsComposerAndNavigation() {
        chat("light",2f)
        compose.onNodeWithContentDescription("Проекты и чаты").assertIsDisplayed()
        compose.onNodeWithContentDescription("Файлы проекта").assertIsDisplayed()
        compose.onNodeWithText("Сообщение для Codex").performTextInput("Многострочный\nчерновик гостя")
        compose.onNodeWithContentDescription("Отправить сообщение").assertIsDisplayed()
        capture("chat-large-font")
    }
    @Test fun ownerQuotaFormAt200PercentFontRemainsScrollable() {
        compose.setContent {MaterialTheme(colorScheme=lightPalette,typography=appTypography) {
            CompositionLocalProvider(LocalDensity provides Density(LocalDensity.current.density,2f)) {
                GuestAccessScreen("https://fixture.invalid","a".repeat(64),{}) {_,_->JSONObject().put("guests",JSONArray())}
            }
        }}
        compose.onNodeWithText("Пригласить гостя").performScrollTo().performClick()
        compose.onNodeWithText("Имя гостя").performTextInput("Длинное синтетическое имя для проверки")
        compose.onNodeWithText("Создать приглашение").performScrollTo().assertIsDisplayed()
        capture("owner-large-font")
    }
}
