plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "ru.wilmain.codexphone"
    compileSdk = 35

    defaultConfig {
        applicationId = "ru.wilmain.codexphone"
        minSdk = 26
        targetSdk = 35
        versionCode = 31
        versionName = "0.9.16"
    }

    signingConfigs {
        getByName("debug") {
            System.getenv("RV_ANDROID_KEYSTORE")?.let { keyPath ->
                storeFile = file(keyPath)
                storePassword = System.getenv("RV_ANDROID_STORE_PASSWORD")
                keyAlias = System.getenv("RV_ANDROID_KEY_ALIAS")
                keyPassword = System.getenv("RV_ANDROID_KEY_PASSWORD")
            }
        }
    }

    flavorDimensions += "connection"
    productFlavors {
        create("personal") {
            dimension = "connection"
            buildConfigField("Boolean", "RELAY_ONLY", "false")
        }
        create("relay") {
            dimension = "connection"
            applicationIdSuffix = ".relay"
            buildConfigField("Boolean", "RELAY_ONLY", "true")
        }
    }

    buildFeatures { compose = true; buildConfig = true }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

dependencies {
    implementation(platform("androidx.compose:compose-bom:2025.06.01"))
    implementation("androidx.activity:activity-compose:1.10.1")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.9.1")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.10.2")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("com.google.android.gms:play-services-code-scanner:16.1.0")
    debugImplementation("androidx.compose.ui:ui-tooling")
}
