package ru.wilmain.codexphone

import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.graphics.toPixelMap
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class GuestAccessUiTest {
    @get:Rule val compose=createComposeRule()

    @Test fun savedGuestConnectionCanBeReopenedWithoutOwnerPairing() {
        val context=androidx.test.platform.app.InstrumentationRegistry.getInstrumentation().targetContext
        val index=context.getSharedPreferences("guest-profiles",0)
        val profile=context.getSharedPreferences("guest-fixture-nav",0)
        index.edit().putString("fixture-nav","codexphone://invite?profile=fixture-nav").commit()
        profile.edit().putString("name","Сохранённый тестовый гость").commit()
        var opened=""
        try {
            compose.setContent {GuestConnectionsScreen({opened=it},{})}
            compose.onNodeWithText("Сохранённый тестовый гость").assertExists().performClick()
            assertEquals("codexphone://invite?profile=fixture-nav",opened)
            assertTrue(index.contains("fixture-nav"))
            compose.onNodeWithText("Подключить свой компьютер").assertExists()
        } finally {index.edit().remove("fixture-nav").commit();profile.edit().clear().commit()}
    }

    @Test fun darkSettingsHaveDarkSurface() {
        compose.setContent {MaterialTheme(colorScheme=darkPalette,typography=appTypography) {
            GuestAccessScreen("https://fixture.invalid","a".repeat(64),{}) {_,_->JSONObject().put("guests",org.json.JSONArray())}
        }}
        compose.waitForIdle()
        val bitmap=compose.onRoot().captureToImage()
        val color=bitmap.toPixelMap()[1,bitmap.height/2]
        assertTrue("Dark surface must not be white",color.red<0.25f&&color.green<0.25f&&color.blue<0.25f)
    }

    @Test fun lightSettingsHaveLightSurface() {
        compose.setContent {MaterialTheme(colorScheme=lightPalette,typography=appTypography) {
            GuestAccessScreen("https://fixture.invalid","a".repeat(64),{}) {_,_->JSONObject().put("guests",org.json.JSONArray())}
        }}
        compose.waitForIdle()
        val bitmap=compose.onRoot().captureToImage()
        val color=bitmap.toPixelMap()[1,bitmap.height/2]
        assertTrue("Light surface must not be dark",color.red>0.9f&&color.green>0.9f&&color.blue>0.9f)
    }

    @Test fun independentBudgetsAndStableInviteRetry() {
        val attempts=mutableListOf<JSONObject>()
        compose.setContent {
            MaterialTheme(colorScheme=lightPalette,typography=appTypography) {
                GuestAccessScreen("https://fixture.invalid", "a".repeat(64),{}) {path,payload->
                    if(path=="guests/invite") {
                        synchronized(attempts) {attempts.add(JSONObject(payload!!.toString()))}
                        if(attempts.size==1) throw IllegalStateException("Ответ потерян")
                        JSONObject().put("guestId","fixture-guest")
                    } else JSONObject().put("guests",org.json.JSONArray())
                }
            }
        }
        compose.onNodeWithText("Пригласить гостя").performScrollTo().performClick()
        compose.onNodeWithText("Имя гостя").performTextInput("Тестовый гость")
        compose.onNodeWithText("Создать приглашение").performScrollTo().performClick()
        compose.waitUntil(5000) {synchronized(attempts){attempts.size==1}}
        compose.onNodeWithText("Повторить запрос").performScrollTo().performClick()
        compose.waitUntil(5000) {synchronized(attempts){attempts.size==2}}
        assertEquals(attempts[0].getString("operationId"),attempts[1].getString("operationId"))
        assertEquals(attempts[0].getString("secret"),attempts[1].getString("secret"))
        val quotas=attempts[1].getJSONObject("quotas")
        assertEquals("unlimited",quotas.getJSONObject("fiveHours").getString("mode"))
        assertEquals("fixed",quotas.getJSONObject("week").getString("mode"))
        assertEquals(15.0,quotas.getJSONObject("week").getDouble("amount"),0.0)
    }

    @Test fun darkThemeAndRevokeConfirmation() {
        var revoked=false
        val guest=JSONObject().put("id","fixture-guest").put("name","Тестовый гость")
            .put("quotas",JSONObject().put("fiveHours",JSONObject().put("rule",JSONObject().put("mode","unlimited")))
                .put("week",JSONObject().put("rule",JSONObject().put("mode","fixed")).put("allocated",15).put("remaining",12)))
        compose.setContent {
            MaterialTheme(colorScheme=darkPalette,typography=appTypography) {
                GuestAccessScreen("https://fixture.invalid","a".repeat(64),{}) {path,_->
                    if(path=="guests/action") {revoked=true;JSONObject()}
                    else JSONObject().put("guests",org.json.JSONArray().put(guest))
                }
            }
        }
        compose.onNodeWithText("Отозвать доступ").performScrollTo().performClick()
        compose.onNodeWithText("Отозвать доступ?").assertExists()
        assertFalse(revoked)
        compose.onNodeWithText("Отмена").performClick()
        assertFalse(revoked)
        compose.onNodeWithText("Отозвать доступ").performClick()
        compose.onNodeWithText("Отозвать",useUnmergedTree=true).performClick()
        compose.waitUntil(5000) {revoked}
    }
}
