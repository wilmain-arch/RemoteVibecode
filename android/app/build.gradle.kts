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
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        versionCode = 48
        versionName = "1.0.0"
    }

    // Isolated package for device tests with synthetic credentials and notification settings.
    if (providers.gradleProperty("taskFixture").orNull == "true") {
        buildTypes.getByName("debug").applicationIdSuffix = ".taskfixture"
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

    buildTypes.getByName("release") {
        isDebuggable = false
        isMinifyEnabled = false
        // Preserve the existing user's signing identity when switching from debug to release.
        signingConfig = signingConfigs.getByName("debug")
    }
    lint { abortOnError = true }
    adbOptions { installOptions.add("-g") }

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
    debugImplementation("androidx.compose.ui:ui-test-manifest")
    androidTestImplementation(platform("androidx.compose:compose-bom:2025.06.01"))
    androidTestImplementation("androidx.compose.ui:ui-test-junit4")
    androidTestImplementation("androidx.test:runner:1.6.2")
    androidTestImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")
    androidTestImplementation("com.squareup.okhttp3:okhttp-tls:4.12.0")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
}
