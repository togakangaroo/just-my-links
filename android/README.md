# Just My Links — Android App

Native Android app for the Just My Links bookmarking service. Features an in-app WebView browser (with bookmark saving), semantic search, and share intent support from Chrome.

## Prerequisites

Install Android command-line tools via Homebrew:

```bash
brew install --cask android-commandlinetools
```

Then install the required SDK components (accept licenses first):

```bash
export ANDROID_HOME=/opt/homebrew/share/android-commandlinetools
yes | sdkmanager --licenses
sdkmanager "platforms;android-34" "platform-tools" "emulator" "system-images;android-34;google_apis_playstore;arm64-v8a"
```

Create an AVD (emulator device):

```bash
avdmanager create avd -n Pixel_8_API34 -k "system-images;android-34;google_apis_playstore;arm64-v8a" --device "pixel_8"
```

## Build

From the `android/` directory:

```bash
./gradlew assembleDebug
```

The APK is output to `app/build/outputs/apk/debug/app-debug.apk`.

## Run on Emulator

Start the emulator (must be run from an interactive terminal to get a visible window):

```bash
ANDROID_HOME=/opt/homebrew/share/android-commandlinetools \
  /opt/homebrew/share/android-commandlinetools/emulator/emulator -avd Pixel_8_API34 &
```

Wait for the emulator to fully boot (home screen visible), then install and launch:

```bash
adb install app/build/outputs/apk/debug/app-debug.apk
adb shell am start -n com.justmylinks/.MainActivity
```

`adb` is at `/opt/homebrew/share/android-commandlinetools/platform-tools/adb` — add it to your PATH for convenience.

### Typing into the emulator

The emulator accepts keyboard input directly from your Mac — just click a field and type. To input text programmatically (useful for long URLs or tokens):

```bash
adb shell input text "your-text-here"
```

Note: special characters (`&`, `#`, spaces) must be escaped. For simple URLs and tokens this works fine.

To return to the home screen: swipe up from the bottom of the emulator, or press the home button in the emulator chrome.

## Iterating

After code changes, rebuild and reinstall in one step:

```bash
./gradlew assembleDebug && \
  adb install app/build/outputs/apk/debug/app-debug.apk && \
  adb shell am start -n com.justmylinks/.MainActivity
```

## First-time Setup (in the app)

1. Open the app and tap **Settings** (gear icon)
2. Enter your API URL (e.g. `https://xxx.execute-api.us-east-1.amazonaws.com/dev`)
3. Enter your bearer token
4. Tap **Save**

The bearer token is stored in `EncryptedSharedPreferences` on the device.

## Features

- **Browser tab**: In-app WebView with a URL bar and bookmark button. Tap the bookmark icon to save the current page to your Just My Links backend.
- **Search tab**: Semantic search across your bookmarks via the backend API. Results are grouped by vector (semantic), title, and tag matches. Tap any result to open it in the browser.
- **Settings tab**: Store your API base URL and bearer token.
- **Share intent**: Share any URL from Chrome (or another browser) directly into the app via the Android share sheet — the link opens automatically in the in-app browser.
