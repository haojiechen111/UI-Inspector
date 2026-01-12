# Car UI Inspector - Android Studio Plugin

This is the Android Studio implementation of the Car UI Inspector tool.

## Prerequisites
- Android Studio (Flamingo or newer recommended)
- JDK 17
- Python 3 with `fastapi`, `uvicorn`, `adbutils`, `pillow` installed (`pip install -r server/requirements.txt`)

## How to Build & Install
1. Open this folder (`android_studio_plugin`) in Android Studio or IntelliJ IDEA.
2. The project will automatically sync with Gradle.
3. Run the task `./gradlew buildPlugin` from the terminal or Gradle tool window.
4. The generated plugin zip will be in `build/distributions/`.
5. In Android Studio, go to `Settings` -> `Plugins` -> `⚙️` -> `Install Plugin from Disk...` and select the zip.

## Features
- Real-time Car UI mirroring in a Tool Window.
- Multi-display support (Display 0, 2, 4, 5).
- High-performance ADB capture (300ms refresh).
- Integrated Python backend logic.

## Project Structure
- `src/`: Kotlin source code for the IDE integration.
- `server/`: Python backend and static Web UI assets.
