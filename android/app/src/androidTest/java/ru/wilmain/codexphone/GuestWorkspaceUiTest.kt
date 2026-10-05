package ru.wilmain.codexphone

import android.graphics.Bitmap
import android.util.Base64
import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.junit4.StateRestorationTester
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.json.JSONObject
import org.json.JSONArray
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import java.io.ByteArrayOutputStream

@RunWith(AndroidJUnit4::class)
class GuestWorkspaceUiTest {
    @get:Rule val compose=createComposeRule()
    @Test fun sharedProjectReadOnlyFilesHaveNoUploadOrWorkAction() {
        compose.setContent {MaterialTheme(colorScheme=darkPalette,typography=appTypography) {
            GuestSharedScreen({}, {}) {path,body->
                check(body==null)
                if(path=="guest/shared") JSONObject().put("resources",JSONArray().put(JSONObject()
                    .put("kind","project").put("resourceId","fixture-project").put("name","Проект друзей").put("right","view")
                    .put("threads",JSONArray().put(JSONObject().put("id","fixture-thread").put("name","Чат проекта")))))
                else JSONObject().put("path","").put("entries",JSONArray())
            }
        }}
        compose.onNodeWithText("Проект друзей").assertExists()
        compose.onNodeWithText("Чат проекта").assertExists()
        compose.onNodeWithText("Работать в моей копии").assertDoesNotExist()
        compose.onNodeWithText("Просмотреть файлы").performClick()
        compose.onNodeWithText("Общие файлы").assertExists()
        compose.onNodeWithText("Загрузить файл · до 4 МиБ").assertDoesNotExist()
    }
    @Test fun sharedHistoryUsesNativeCursorAndLoadsEarlierMessages() {
        val paths=mutableListOf<String>()
        compose.setContent {MaterialTheme(colorScheme=lightPalette,typography=appTypography) {
            GuestSharedScreen({}, {}) {path,_->
                paths.add(path)
                if(path=="guest/shared") JSONObject().put("resources",JSONArray().put(JSONObject().put("kind","thread").put("resourceId","fixture-thread").put("name","Общий чат").put("right","view")))
                else if(path.contains("before=history-cursor"))JSONObject().put("turns",JSONArray().put(JSONObject().put("text","Раннее сообщение"))).put("nextBefore",JSONObject.NULL)
                else JSONObject().put("turns",JSONArray().put(JSONObject().put("text","Последнее сообщение"))).put("nextBefore","history-cursor")
            }
        }}
        compose.onNodeWithText("Просмотреть историю чата").performClick()
        compose.onNodeWithText("Показать ранние сообщения").performScrollTo().performClick()
        compose.waitUntil(5000){paths.any {it.contains("before=history-cursor")}}
        compose.onNodeWithText("Раннее сообщение",substring=true).assertExists()
        compose.onNodeWithText("Показать ранние сообщения").assertDoesNotExist()
    }

    @Test fun failedPreviewCanBeRetriedWithoutLosingSelectedFile() {
        var attempts=0
        compose.setContent {MaterialTheme(colorScheme=lightPalette,typography=appTypography) {
            GuestWorkspaceScreen("default",{},initialPath="demo.txt",onRequest={path,_->
                if(path.contains("blob=true")) {
                    attempts++
                    if(attempts==1)error("Синтетическая ошибка файла")
                    JSONObject().put("name","demo.txt").put("mime","text/plain").put("dataBase64","c3ludGhldGlj")
                } else {kotlinx.coroutines.delay(100);JSONObject().put("entries",JSONArray())}
            })
        }}
        compose.waitUntil(5000){compose.onAllNodesWithText("Повторить открытие файла").fetchSemanticsNodes().isNotEmpty()}
        compose.onNodeWithText("Повторить открытие файла").performScrollTo().performClick()
        compose.onNodeWithText("synthetic").assertExists()
        compose.onNodeWithText("Скачать файл").performScrollTo().assertExists()
        org.junit.Assert.assertEquals(2,attempts)
    }

    @Test fun selectedImageRestoresAndHasDownloadAndZoomActions() {
        val output=ByteArrayOutputStream()
        val bitmap=Bitmap.createBitmap(8,8,Bitmap.Config.ARGB_8888)
        bitmap.compress(Bitmap.CompressFormat.PNG,100,output);bitmap.recycle()
        val data=Base64.encodeToString(output.toByteArray(),Base64.NO_WRAP)
        val restoration=StateRestorationTester(compose)
        restoration.setContent {MaterialTheme(colorScheme=lightPalette,typography=appTypography) {
            GuestWorkspaceScreen("default",{},onRequest={path,_->
                if(path.contains("blob=true")) JSONObject().put("name","fixture.png").put("mime","image/png").put("dataBase64",data)
                else JSONObject().put("path","").put("entries",JSONArray().put(JSONObject().put("name","fixture.png").put("path","fixture.png").put("isDirectory",false)))
            })
        }}
        compose.onNodeWithText("fixture.png").performClick()
        compose.onNodeWithText("Сбросить масштаб").performScrollTo().assertExists()
        compose.onNodeWithText("Скачать файл").performScrollTo().assertExists()
        restoration.emulateSavedInstanceStateRestore()
        compose.onNodeWithText("Сбросить масштаб").performScrollTo().assertExists()
        compose.onNodeWithContentDescription("fixture.png. Масштабирование двумя пальцами").assertExists()
    }
}
