plugins {
    id("java")
    id("org.jetbrains.intellij") version "1.18.0"
    kotlin("jvm") version "1.9.10"
}

group = "com.carui"
version = "1.0-SNAPSHOT"

repositories {
    mavenCentral()
}

intellij {
    // 适配 Android Studio 2024.1+ (Ladybug 使用的是 241+ 底层)
    version.set("2024.1")
    type.set("IC") 
    updateSinceUntilBuild.set(false)
}

tasks {
    patchPluginXml {
        sinceBuild.set("241")
        untilBuild.set("261.*")
    }

    withType<JavaCompile> {
        sourceCompatibility = "17"
        targetCompatibility = "17"
        options.release.set(17)
    }
    withType<org.jetbrains.kotlin.gradle.tasks.KotlinCompile> {
        kotlinOptions.jvmTarget = "17"
    }
}
