plugins {
    id("java")
    id("org.jetbrains.intellij") version "1.17.3"
    kotlin("jvm") version "1.9.10"
}

group = "com.carui"

// 自动版本号管理
val versionFile = file("version.txt")
val currentVersion = if (versionFile.exists()) {
    versionFile.readText().trim()
} else {
    "1.0.0"
}

// 解析版本号并递增
fun incrementVersion(version: String): String {
    val parts = version.split(".")
    if (parts.size != 3) return "1.0.0"
    
    var major = parts[0].toInt()
    var minor = parts[1].toInt()
    var patch = parts[2].toInt()
    
    patch++
    if (patch >= 10) {
        patch = 0
        minor++
        if (minor >= 10) {
            minor = 0
            major++
        }
    }
    
    return "$major.$minor.$patch"
}

// 在构建时递增版本号
val newVersion = incrementVersion(currentVersion)
version = newVersion

// 保存新版本号
tasks.register("updateVersion") {
    doLast {
        versionFile.writeText(newVersion)
        println("✅ 版本号已更新: $currentVersion -> $newVersion")
    }
}

// 在buildPlugin之前自动更新版本号
tasks.named("buildPlugin") {
    dependsOn("updateVersion")
}

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

    buildSearchableOptions {
        enabled = false // Speed up build
    }

    prepareSandbox {
        // 将插件目录下的 server 文件夹拷贝到安装包中
        from("server") {
            into("${intellij.pluginName.get()}/server")
        }
    }
}
