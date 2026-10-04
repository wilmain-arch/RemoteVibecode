#!/usr/bin/env bash
# CI fixture only: never collect data from the user's primary package.
set -uo pipefail
./android/gradlew -p android -PtaskFixture=true connectedRelayDebugAndroidTest
result=$?
mkdir -p android/app/build/reports/androidTests/diagnostic
adb exec-out run-as ru.wilmain.codexphone.relay.taskfixture tar -cf - cache > android/app/build/reports/androidTests/diagnostic/synthetic-cache.tar || true
adb logcat -d -s RV-PREVIEW > android/app/build/reports/androidTests/diagnostic/preview.log || true
exit "$result"
