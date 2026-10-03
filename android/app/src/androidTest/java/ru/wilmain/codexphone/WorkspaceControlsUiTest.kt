package ru.wilmain.codexphone

import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.Density
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File

@RunWith(AndroidJUnit4::class)
class WorkspaceControlsUiTest {
    @get:Rule val compose = createComposeRule()
    private fun show(dark:Boolean=false, font:Float=1f, turn:String="", compatibility:String="", request:suspend (String,JSONObject?)->JSONObject={_,_->JSONObject()}, jump:(String)->Unit={}) {
        compose.setContent {
            val density=LocalDensity.current
            CompositionLocalProvider(LocalDensity provides Density(density.density,font)) {
                MaterialTheme(colorScheme=if(dark) darkPalette else lightPalette, typography=appTypography) {
                    androidx.compose.material3.Surface { WorkspaceControls("fixture","Синтетический проект с длинным русским названием",turn,request,{}, {},jump,{},compatibilityNote=compatibility) }
                }
            }
        }
    }
    private fun shot(name:String) {
        val ctx=InstrumentationRegistry.getInstrumentation().targetContext
        compose.waitForIdle()
        Thread.sleep(500) // Allow the separate dialog window transition to finish before capture.
        val screenshot=InstrumentationRegistry.getInstrumentation().uiAutomation.takeScreenshot()
        File(ctx.filesDir,"$name.png").outputStream().use { stream -> screenshot.compress(android.graphics.Bitmap.CompressFormat.PNG,100,stream) }
        screenshot.recycle()
    }
    @Test fun oldAgentShowsCompatibilityNoticeWithoutActions() {
        var requests=0
        show(compatibility="Обновите агент на ПК", request={_,_->requests++; JSONObject()})
        compose.onNodeWithText("Обновите агент на ПК").assertIsDisplayed()
        compose.onNodeWithText("Создать новый план").assertDoesNotExist()
        assertEquals(0,requests)
        shot("completion-compatibility-light")
    }

    @Test fun newPlanShowsCurrentAndKeepsRetryIdentity() {
        val payloads=mutableListOf<JSONObject>()
        show(request={_,payload->
            if(payload!=null) { payloads.add(JSONObject(payload.toString())); error("Тестовый обрыв связи") }
            JSONObject("""{"modes":[{"mode":"plan"},{"mode":"default"}],"selectedMode":"default","currentPlanTurnId":"new","plans":[{"turnId":"old","text":"Старый план"},{"turnId":"new","text":"Новый план"}]}""")
        })
        compose.onNodeWithText("План и цель").performScrollTo().performClick()
        compose.waitUntil { compose.onAllNodesWithText("Новый план").fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithText("Старый план").assertDoesNotExist()
        compose.onNodeWithText("Предыдущие планы · 1").assertDoesNotExist()
        compose.onNodeWithText("Создать новый план").performScrollTo().performClick()
        compose.onNodeWithText("Создать план").assertIsNotEnabled()
        compose.onNodeWithText("Что нужно распланировать").performTextInput("Синтетическая задача")
        compose.onNodeWithText("Создать план").performClick()
        compose.waitUntil { payloads.size==1 }
        compose.onNodeWithText("Тестовый обрыв связи").assertIsDisplayed()
        compose.onNodeWithText("Создать план").performClick()
        compose.waitUntil { payloads.size==2 }
        assertEquals(payloads[0].getString("operationId"),payloads[1].getString("operationId"))
        assertEquals("plan-create",payloads[0].getString("action"))
        assertTrue(payloads[0].getBoolean("confirmed"))
    }

    @Test fun cancelKeepsPlanAndAcceptedLaunchClearsIt() {
        var accepted=false
        show(dark=true,request={_,payload->
            if(payload!=null) { accepted=true; JSONObject("""{"threadId":"fixture","turnId":"accepted"}""") }
            else if(accepted) JSONObject("""{"modes":[{"mode":"plan"}],"currentPlanTurnId":"accepted","currentPlanStatus":"inProgress","plans":[]}""")
            else JSONObject("""{"modes":[{"mode":"plan"}],"currentPlanTurnId":"old","plans":[{"turnId":"old","text":"Прежний план"}]}""")
        })
        compose.onNodeWithText("План и цель").performScrollTo().performClick()
        compose.waitUntil { compose.onAllNodesWithText("Прежний план").fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithText("Создать новый план").performScrollTo().performClick()
        compose.onNodeWithText("Что нужно распланировать").performTextInput("Отменяемая задача")
        compose.onNodeWithText("Отмена").performClick()
        assertFalse(accepted)
        compose.onNodeWithText("Прежний план").assertExists()
        compose.onNodeWithText("Создать новый план").performScrollTo().performClick()
        compose.onNodeWithText("Что нужно распланировать").performTextInput("Новая синтетическая задача")
        compose.onNodeWithText("Создать план").performClick()
        compose.waitUntil { compose.onAllNodesWithText("Codex составляет новый план. Ход работы доступен в чате.").fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithText("Прежний план").assertDoesNotExist()
        compose.onNodeWithContentDescription("Обновить раздел").performClick()
        compose.waitUntil { compose.onAllNodesWithText("Codex составляет новый план. Ход работы доступен в чате.").fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithText("Прежний план").assertDoesNotExist()
        shot("plan-clear-dark")
    }

    @Test fun renameRequiresTextAndSendsOneAction() {
        var body:JSONObject?=null
        show(request={_,payload->if(payload!=null)body=payload;JSONObject()})
        compose.onNodeWithText("Переименовать").performClick()
        compose.onNodeWithText("Название").performTextClearance()
        compose.onNodeWithText("Подтвердить").assertIsNotEnabled()
        compose.onNodeWithText("Название").performTextReplacement("Новое имя")
        compose.onNodeWithText("Подтвердить").performClick()
        compose.waitUntil { body!=null }
        assertEquals("rename",body?.optString("action"));assertEquals("Новое имя",body?.optString("name"))
    }
    @Test fun revertExplainsFilesAndNeedsConfirmationDark() {
        var calls=0
        show(dark=true,turn="turn",request={_,payload->if(payload!=null)calls++;JSONObject()})
        compose.onNode(hasScrollToIndexAction()).performScrollToNode(hasText("Откатить до этого хода"))
        compose.onNodeWithText("Откатить до этого хода").performClick()
        compose.onNodeWithText("Выбранный ход и последующие сообщения будут удалены из истории. Изменения файлов НЕ откатываются. Действие необратимо.").assertIsDisplayed()
        assertEquals(0,calls);shot("phase2-revert-dark")
        compose.onNodeWithText("Отмена").performClick();assertEquals(0,calls)
    }
    @Test fun searchFindsMessageAndJumpsToTurn() {
        var jumped=""
        show(request={path,_-> if(path.startsWith("control?"))JSONObject("""{"data":[{"turnId":"found","snippet":"Синтетическое совпадение"}],"nextCursor":null}""") else JSONObject()},jump={jumped=it})
        compose.onNodeWithText("Поиск",substring=false).performClick()
        compose.onNodeWithText("Поиск сообщений в этом чате").performTextInput("совпадение")
        compose.onNodeWithText("Поиск сообщений в этом чате").performImeAction()
        compose.waitUntil(5000){compose.onAllNodesWithText("Синтетическое совпадение").fetchSemanticsNodes().isNotEmpty()}
        compose.waitForIdle()
        shot("phase2-search-light")
        compose.onNode(hasScrollToIndexAction()).performScrollToNode(hasText("Открыть сообщение"))
        compose.onNodeWithText("Открыть сообщение").performClick();compose.waitUntil { jumped.isNotBlank() };assertEquals("found",jumped)
    }
    @Test fun searchErrorShowsRetry() {
        var fail=true
        show(request={path,_-> if(path.startsWith("control?") && fail)error("Сеть недоступна");JSONObject("""{"data":[],"nextCursor":null}""")})
        compose.onNodeWithText("Поиск").performClick();compose.onNodeWithText("Поиск сообщений в этом чате").performTextInput("test")
        compose.onNodeWithText("Поиск сообщений в этом чате").performImeAction()
        compose.waitUntil(5000){compose.onAllNodesWithText("Сеть недоступна").fetchSemanticsNodes().isNotEmpty()}
        compose.waitForIdle();fail=false;compose.onNodeWithText("Повторить").performScrollTo().performClick()
        compose.waitUntil(5000){compose.onAllNodesWithText("Совпадений нет").fetchSemanticsNodes().isNotEmpty()}
    }
    @Test fun projectDeletePreservesFilesNoticeAtLargeFont() {
        show(font=2f,request={_,_->JSONObject("""{"data":[{"id":"p","name":"Тестовый проект","roots":[{"path":"/synthetic/project"}]}],"nextCursor":null}""")})
        compose.onNodeWithText("Проекты").performScrollTo().performClick()
        compose.waitUntil { compose.onAllNodesWithText("Тестовый проект").fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithText("Удалить").performScrollTo().performClick()
        compose.onNodeWithText("Папка и файлы на ПК останутся. Чаты не удаляются.").assertIsDisplayed()
        shot("phase2-project-delete-200")
    }
    @Test fun queueDoesNotPretendItWaitsWhenIdle() {
        var calls=0
        show(request={_,payload->if(payload!=null)calls++;JSONObject("""{"data":[],"nextCursor":null}""")})
        compose.onNodeWithText("Очередь Desktop").performScrollTo().performClick()
        compose.onNodeWithText("Добавить сообщение").performScrollTo().performClick()
        compose.onNodeWithText("В свободном чате сообщение может начать выполняться сразу и расходовать лимиты.").assertIsDisplayed()
        compose.onNodeWithText("Сообщение").performTextInput("Тест без запуска")
        compose.onNodeWithText("Отмена").performClick();assertEquals(0,calls)
    }
    @Test fun gitReviewIsExplicitAndPreservesStateAfterFailure() {
        show(dark=true,request={_,payload->if(payload!=null)error("Тестовый отказ");JSONObject("""{"branch":"main","status":"","branches":["main"],"diff":"","worktrees":"synthetic","nextCursor":null}""")})
        compose.onNodeWithText("Git").performScrollTo().performClick()
        compose.onNode(hasScrollToIndexAction()).performScrollToNode(hasText("Запустить ревью"))
        compose.onNodeWithText("Запустить ревью").performClick()
        compose.onNodeWithText("Codex проверит незакоммиченные изменения. Это модельная задача, она расходует лимит. Результат появится в этом чате.").assertIsDisplayed()
        compose.onNodeWithText("Подтвердить").performClick()
        compose.waitUntil{compose.onAllNodesWithText("Тестовый отказ").fetchSemanticsNodes().isNotEmpty()}
        compose.onNodeWithText("Запустить ревью?").assertIsDisplayed()
    }
    @Test fun dialogDraftSurvivesStateRestoration() {
        val restore=androidx.compose.ui.test.junit4.StateRestorationTester(compose)
        restore.setContent {
            MaterialTheme(colorScheme=lightPalette,typography=appTypography) {
                androidx.compose.material3.Surface {
                    WorkspaceControls("fixture","Тестовый чат",request={_,_->JSONObject()},onClose={},onSelect={},onJump={},onChanged={})
                }
            }
        }
        compose.onNodeWithText("Переименовать").performClick()
        compose.onNodeWithText("Название").performTextReplacement("Сохранённый черновик")
        restore.emulateSavedInstanceStateRestore()
        compose.onNodeWithText("Название чата").assertIsDisplayed()
        compose.onNodeWithText("Сохранённый черновик").assertIsDisplayed()
    }

}
