import QtQuick
import QtQuick.Controls

Item {
    id: rootOverlay
    anchors.fill: parent

    // =========================================================================
    // COLOR PALETTE — Jarvis Cyan / Electric Blue Glass HUD
    // =========================================================================
    readonly property color colNeonCyan: "#00F0FF"
    readonly property color colElectricBlue: "#38BDF8"
    readonly property color colDeepBlue: "#0EA5E9"
    readonly property color colDarkGlass: "#E8080D16"
    readonly property color colBorderCyan: "#6600F0FF"
    readonly property color colFillCyan: "#1A00F0FF"
    readonly property color colTextWhite: "#F8FAFC"
    readonly property color colTextMuted: "#94A3B8"

    // =========================================================================
    // STATE PROPERTIES
    // =========================================================================
    property string islandState: "idle"     // "idle", "listening", "thinking", "locating", "acting", "speaking", "assist"
    property string statusText: ""
    property string subText: ""
    property bool assistModeActive: false
    property bool idleAutoHide: false       // Sleek mini-pill stays docked at top by default
    property int idleAutoHideMs: 15000      // 15 seconds grace period when idle auto-hide is enabled
    property bool islandHidden: false

    readonly property bool isIdle: (islandState === "idle")

    // Ghost cursor properties
    property real ghostX: width > 0 ? width / 2 : 960
    property real ghostY: 28
    property real ghostTargetX: -100
    property real ghostTargetY: -100
    property string ghostLabel: ""
    property real ghostOpacity: 0.0
    property int ghostDurationMs: 380
    property bool ghostPulseOnArrival: false

    Component.onCompleted: {
        ghostX = width > 0 ? width / 2 : 960;
        ghostY = 28;
        if (islandState === "idle" && idleAutoHide && !assistModeActive) {
            idleAutoHideTimer.restart();
        }
    }

    // Pulse target properties
    property real pulseX: 0
    property real pulseY: 0
    property string pulseLabel: ""

    // =========================================================================
    // HIGHLIGHT MODEL
    // =========================================================================
    ListModel {
        id: highlightModel
    }

    // =========================================================================
    // TIMERS
    // =========================================================================
    // Auto-idle timer (for transitions back to idle after auto_idle_sec)
    Timer {
        id: autoIdleTimer
        repeat: false
        onTriggered: {
            setIsland("idle", "", "", 0);
        }
    }

    // Auto-hide timer for Top Dynamic Island when idle
    Timer {
        id: idleAutoHideTimer
        interval: idleAutoHideMs
        repeat: false
        onTriggered: {
            // NEVER hide the island when Assist Mode is active or auto-hide is disabled!
            if (islandState === "idle" && !assistModeActive && idleAutoHide) {
                islandHidden = true;
            }
        }
    }

    // Auto-fade timer for Ghost Cursor
    Timer {
        id: ghostAutoHideTimer
        interval: 4000
        repeat: false
        onTriggered: {
            if (highlightModel.count === 0) {
                ghostOpacity = 0.0;
            }
        }
    }

    // Auto-clear timer for Highlights
    Timer {
        id: highlightTimer
        repeat: false
        onTriggered: {
            clearHighlights();
        }
    }

    // =========================================================================
    // PUBLIC API / CONTROL FUNCTIONS
    // =========================================================================
    function setIsland(stateName, primary, secondary, autoIdleSec) {
        islandState = stateName || "idle";
        statusText = primary || "";
        subText = secondary || "";

        // Waking up from auto-hide
        islandHidden = false;
        idleAutoHideTimer.stop();

        if (islandState === "idle") {
            if (idleAutoHide && !assistModeActive) {
                idleAutoHideTimer.restart();
            }
        } else {
            if (autoIdleSec && autoIdleSec > 0) {
                autoIdleTimer.interval = autoIdleSec * 1000;
                autoIdleTimer.restart();
            } else {
                autoIdleTimer.stop();
            }
        }
    }

    function setAssistBadge(active) {
        assistModeActive = !!active;
        if (assistModeActive) {
            islandHidden = false;
            idleAutoHideTimer.stop();
        } else if (islandState === "idle" && idleAutoHide) {
            idleAutoHideTimer.restart();
        }
    }

    function moveGhost(tx, ty, label, durationMs, pulse) {
        var samePos = (Math.abs(ghostX - tx) < 1 && Math.abs(ghostY - ty) < 1);
        ghostTargetX = tx;
        ghostTargetY = ty;
        ghostLabel = label || "";
        ghostDurationMs = durationMs > 0 ? durationMs : 380;
        ghostPulseOnArrival = !!pulse;

        ghostOpacity = 1.0;
        ghostAutoHideTimer.interval = 4000;
        ghostAutoHideTimer.restart();

        // Trigger movement from current position (or Top Island on first show) to (tx, ty)
        ghostX = tx;
        ghostY = ty;
        if (samePos && ghostPulseOnArrival) {
            ghostRippleAnim.restart();
        }
    }

    function hideGhost() {
        ghostOpacity = 0.0;
        ghostAutoHideTimer.stop();
    }

    function displayHighlights(boxes, duration, moveGhostToFirst) {
        highlightModel.clear();
        if (!boxes || boxes.length === 0) {
            highlightTimer.stop();
            return;
        }

        var firstBox = null;
        for (var i = 0; i < boxes.length; ++i) {
            var b = boxes[i];
            var bx = 0, by = 0, bw = 0, bh = 0, blabel = "";
            if (b.x !== undefined) {
                bx = b.x; by = b.y; bw = b.width; bh = b.height;
                blabel = b.label || b.text || "";
            } else if (b.left !== undefined) {
                bx = b.left; by = b.top; bw = b.width; bh = b.height;
                blabel = b.label || b.text || "";
            } else if (b.box) {
                bx = b.box[0]; by = b.box[1]; bw = b.box[2]; bh = b.box[3];
                blabel = b.label || b.text || "";
            }
            highlightModel.append({
                boxX: bx,
                boxY: by,
                boxW: bw,
                boxH: bh,
                boxLabel: blabel
            });
            if (i === 0) {
                firstBox = { x: bx, y: by, w: bw, h: bh, label: blabel };
            }
        }

        if (duration && duration > 0) {
            highlightTimer.interval = duration * 1000;
            highlightTimer.restart();
        }

        if (moveGhostToFirst && firstBox) {
            var cx = firstBox.x + Math.round(firstBox.w / 2);
            var cy = firstBox.y + Math.round(firstBox.h / 2);
            moveGhost(cx, cy, firstBox.label, 360, true);
        }
    }

    function clearHighlights() {
        highlightModel.clear();
        highlightTimer.stop();
        ghostAutoHideTimer.interval = 1200;
        ghostAutoHideTimer.restart();
    }

    function triggerPulse(px, py, plabel) {
        pulseX = px;
        pulseY = py;
        pulseLabel = plabel || "";
        pulseAnim.restart();
    }

    // =========================================================================
    // QOBJECT BRIDGE CONNECTIONS (if bridge is provided via context)
    // =========================================================================
    Connections {
        target: (typeof overlayBridge !== "undefined") ? overlayBridge : null
        ignoreUnknownSignals: true
        function onSigIslandState(stateName, primary, secondary, autoIdleSec) {
            setIsland(stateName, primary, secondary, autoIdleSec);
        }
        function onSig_island_state(stateName, primary, secondary, autoIdleSec) {
            setIsland(stateName, primary, secondary, autoIdleSec);
        }
        function onSigAssistBadge(active) {
            setAssistBadge(active);
        }
        function onSig_assist_badge(active) {
            setAssistBadge(active);
        }
        function onSigGhostMove(x, y, label, durationMs, pulse) {
            moveGhost(x, y, label, durationMs, pulse);
        }
        function onSig_ghost_move(x, y, label, durationMs, pulse) {
            moveGhost(x, y, label, durationMs, pulse);
        }
        function onSigGhostHide() {
            hideGhost();
        }
        function onSig_ghost_hide() {
            hideGhost();
        }
        function onSigShowHighlights(boxes, duration, moveG) {
            displayHighlights(boxes, duration, moveG);
        }
        function onSig_show_highlights(boxes, duration, moveG) {
            displayHighlights(boxes, duration, moveG);
        }
        function onSigClearHighlights() {
            clearHighlights();
        }
        function onSig_clear_highlights() {
            clearHighlights();
        }
        function onSigPulse(x, y, label) {
            triggerPulse(x, y, label);
        }
        function onSig_pulse(x, y, label) {
            triggerPulse(x, y, label);
        }
    }

    // =========================================================================
    // 1. LASER TARGET HIGHLIGHTS LAYER
    // =========================================================================
    Item {
        id: highlightsLayer
        anchors.fill: parent

        Repeater {
            model: highlightModel
            delegate: Item {
                id: boxDelegate
                x: model.boxX
                y: model.boxY
                width: Math.max(8, model.boxW)
                height: Math.max(8, model.boxH)

                // Smooth fade-in
                opacity: 0.0
                PropertyAnimation {
                    id: boxFadeInAnim
                    target: boxDelegate
                    property: "opacity"
                    to: 1.0
                    duration: 220
                    easing.type: Easing.OutQuad
                }
                Component.onCompleted: boxFadeInAnim.start()

                // Laser glow background & border
                Rectangle {
                    anchors.fill: parent
                    radius: 8
                    color: colFillCyan
                    border.color: colNeonCyan
                    border.width: 2.5

                    // Breathing border aura
                    SequentialAnimation on border.color {
                        loops: Animation.Infinite
                        ColorAnimation { to: colElectricBlue; duration: 900 }
                        ColorAnimation { to: colNeonCyan; duration: 900 }
                    }
                }

                // --- Corner HUD Bracket Accents ---
                // Top-Left Corner
                Rectangle { x: -2; y: -2; width: 14; height: 3; color: colNeonCyan; radius: 1.5 }
                Rectangle { x: -2; y: -2; width: 3; height: 14; color: colNeonCyan; radius: 1.5 }

                // Top-Right Corner
                Rectangle { x: parent.width - 12; y: -2; width: 14; height: 3; color: colNeonCyan; radius: 1.5 }
                Rectangle { x: parent.width - 1; y: -2; width: 3; height: 14; color: colNeonCyan; radius: 1.5 }

                // Bottom-Left Corner
                Rectangle { x: -2; y: parent.height - 1; width: 14; height: 3; color: colNeonCyan; radius: 1.5 }
                Rectangle { x: -2; y: parent.height - 12; width: 3; height: 14; color: colNeonCyan; radius: 1.5 }

                // Bottom-Right Corner
                Rectangle { x: parent.width - 12; y: parent.height - 1; width: 14; height: 3; color: colNeonCyan; radius: 1.5 }
                Rectangle { x: parent.width - 1; y: parent.height - 12; width: 3; height: 14; color: colNeonCyan; radius: 1.5 }

                // Floating label pill above the box (or below if near top of screen)
                Rectangle {
                    visible: model.boxLabel !== ""
                    x: Math.min(parent.width - width, Math.max(0, (parent.width - width) / 2))
                    y: (model.boxY < 28 ? parent.height + 6 : -26)
                    height: 20
                    width: boxLabelText.implicitWidth + 14
                    radius: 6
                    color: colDarkGlass
                    border.color: colNeonCyan
                    border.width: 1.5

                    Text {
                        id: boxLabelText
                        anchors.centerIn: parent
                        text: model.boxLabel
                        color: colNeonCyan
                        font.pixelSize: 10
                        font.bold: true
                    }
                }
            }
        }
    }

    // =========================================================================
    // 2. GHOST CURSOR & MOTION TRAIL LAYER
    // =========================================================================
    Item {
        id: ghostLayer
        anchors.fill: parent
        opacity: ghostOpacity
        Behavior on opacity {
            NumberAnimation { duration: 250; easing.type: Easing.OutCubic }
        }

        // Motion Trail Dot 3 (outermost tail)
        Item {
            id: trail3
            x: ghostX; y: ghostY
            width: 0; height: 0
            Behavior on x { NumberAnimation { duration: Math.round(ghostDurationMs * 1.60); easing.type: Easing.OutCubic } }
            Behavior on y { NumberAnimation { duration: Math.round(ghostDurationMs * 1.60); easing.type: Easing.OutCubic } }
            Rectangle {
                anchors.centerIn: parent
                width: 6; height: 6; radius: 3
                color: colNeonCyan
                opacity: 0.15
            }
        }

        // Motion Trail Dot 2
        Item {
            id: trail2
            x: ghostX; y: ghostY
            width: 0; height: 0
            Behavior on x { NumberAnimation { duration: Math.round(ghostDurationMs * 1.42); easing.type: Easing.OutCubic } }
            Behavior on y { NumberAnimation { duration: Math.round(ghostDurationMs * 1.42); easing.type: Easing.OutCubic } }
            Rectangle {
                anchors.centerIn: parent
                width: 8; height: 8; radius: 4
                color: colElectricBlue
                opacity: 0.28
            }
        }

        // Motion Trail Dot 1
        Item {
            id: trail1
            x: ghostX; y: ghostY
            width: 0; height: 0
            Behavior on x { NumberAnimation { duration: Math.round(ghostDurationMs * 1.25); easing.type: Easing.OutCubic } }
            Behavior on y { NumberAnimation { duration: Math.round(ghostDurationMs * 1.25); easing.type: Easing.OutCubic } }
            Rectangle {
                anchors.centerIn: parent
                width: 11; height: 11; radius: 5.5
                color: colElectricBlue
                opacity: 0.42
            }
        }

        // Motion Trail Dot 0 (near cursor)
        Item {
            id: trail0
            x: ghostX; y: ghostY
            width: 0; height: 0
            Behavior on x { NumberAnimation { duration: Math.round(ghostDurationMs * 1.10); easing.type: Easing.OutCubic } }
            Behavior on y { NumberAnimation { duration: Math.round(ghostDurationMs * 1.10); easing.type: Easing.OutCubic } }
            Rectangle {
                anchors.centerIn: parent
                width: 14; height: 14; radius: 7
                color: colNeonCyan
                opacity: 0.60
            }
        }

        // Main Ghost Cursor Node
        Item {
            id: ghostNode
            x: ghostX
            y: ghostY
            width: 0; height: 0

            Behavior on x {
                NumberAnimation {
                    id: xAnim
                    duration: ghostDurationMs
                    easing.type: Easing.OutCubic
                    onRunningChanged: {
                        if (!xAnim.running && !yAnim.running && ghostPulseOnArrival) {
                            ghostRippleAnim.restart();
                        }
                    }
                }
            }
            Behavior on y {
                NumberAnimation {
                    id: yAnim
                    duration: ghostDurationMs
                    easing.type: Easing.OutCubic
                    onRunningChanged: {
                        if (!xAnim.running && !yAnim.running && ghostPulseOnArrival) {
                            ghostRippleAnim.restart();
                        }
                    }
                }
            }

            // Expanding Sonar Ripple Ring on Arrival
            Rectangle {
                id: ghostSonarRing
                anchors.centerIn: parent
                width: 44; height: 44; radius: 22
                color: "transparent"
                border.color: colNeonCyan
                border.width: 2.0
                scale: 0.3
                opacity: 0.0

                ParallelAnimation {
                    id: ghostRippleAnim
                    NumberAnimation {
                        target: ghostSonarRing
                        property: "scale"
                        from: 0.3; to: 2.4
                        duration: 650
                        easing.type: Easing.OutQuad
                    }
                    NumberAnimation {
                        target: ghostSonarRing
                        property: "opacity"
                        from: 0.95; to: 0.0
                        duration: 650
                        easing.type: Easing.OutQuad
                    }
                }
            }

            // Outer Aura Ring
            Rectangle {
                anchors.centerIn: parent
                width: 28; height: 28; radius: 14
                color: colFillCyan
                border.color: colElectricBlue
                border.width: 1.5

                SequentialAnimation on scale {
                    loops: Animation.Infinite
                    NumberAnimation { to: 1.15; duration: 750; easing.type: Easing.InOutQuad }
                    NumberAnimation { to: 0.90; duration: 750; easing.type: Easing.InOutQuad }
                }
            }

            // Glowing Center Orb
            Rectangle {
                anchors.centerIn: parent
                width: 12; height: 12; radius: 6
                color: colNeonCyan

                // Core White Spark
                Rectangle {
                    anchors.centerIn: parent
                    width: 4; height: 4; radius: 2
                    color: "#FFFFFF"
                }
            }

            // Floating Label Pill next to Ghost Cursor
            Rectangle {
                id: ghostLabelPill
                visible: ghostLabel !== ""
                x: 18; y: -12
                height: 22
                width: ghostLabelText.implicitWidth + 14
                radius: 6
                color: colDarkGlass
                border.color: colNeonCyan
                border.width: 1.5

                Text {
                    id: ghostLabelText
                    anchors.centerIn: parent
                    text: ghostLabel
                    color: colTextWhite
                    font.pixelSize: 10
                    font.bold: true
                }
            }
        }
    }

    // =========================================================================
    // 3. CLICK TARGET PULSE LOCK-ON LAYER
    // =========================================================================
    Item {
        id: pulseLayer
        x: pulseX
        y: pulseY
        width: 0; height: 0
        visible: pulseAnim.running

        // Outer Target Lock Ring
        Rectangle {
            id: pulseRingOuter
            anchors.centerIn: parent
            width: 48; height: 48; radius: 24
            color: "transparent"
            border.color: colNeonCyan
            border.width: 2.5
            scale: 2.0
            opacity: 0.0
        }

        // Inner Shockwave Ring
        Rectangle {
            id: pulseRingInner
            anchors.centerIn: parent
            width: 32; height: 32; radius: 16
            color: "transparent"
            border.color: colElectricBlue
            border.width: 2.0
            scale: 0.4
            opacity: 0.0
        }

        // Crosshair Ticks
        Rectangle { id: tickT; anchors.horizontalCenter: parent.horizontalCenter; y: -24; width: 2; height: 8; color: colNeonCyan }
        Rectangle { id: tickB; anchors.horizontalCenter: parent.horizontalCenter; y: 16; width: 2; height: 8; color: colNeonCyan }
        Rectangle { id: tickL; anchors.verticalCenter: parent.verticalCenter; x: -24; width: 8; height: 2; color: colNeonCyan }
        Rectangle { id: tickR; anchors.verticalCenter: parent.verticalCenter; x: 16; width: 8; height: 2; color: colNeonCyan }

        // Pulse Label
        Rectangle {
            visible: pulseLabel !== ""
            anchors.horizontalCenter: parent.horizontalCenter
            y: 28
            height: 18
            width: pulseLabelText.implicitWidth + 10
            radius: 5
            color: colDarkGlass
            border.color: colNeonCyan
            border.width: 1.0

            Text {
                id: pulseLabelText
                anchors.centerIn: parent
                text: pulseLabel
                color: colNeonCyan
                font.pixelSize: 9
                font.bold: true
            }
        }

        ParallelAnimation {
            id: pulseAnim

            // Outer ring collapses to 1.0 and fades
            NumberAnimation {
                target: pulseRingOuter
                property: "scale"
                from: 2.0; to: 0.95
                duration: 700
                easing.type: Easing.OutCubic
            }
            SequentialAnimation {
                NumberAnimation {
                    target: pulseRingOuter
                    property: "opacity"
                    from: 0.0; to: 1.0
                    duration: 180
                }
                NumberAnimation {
                    target: pulseRingOuter
                    property: "opacity"
                    from: 1.0; to: 0.0
                    duration: 520
                }
            }

            // Inner ring expands out
            NumberAnimation {
                target: pulseRingInner
                property: "scale"
                from: 0.4; to: 2.2
                duration: 700
                easing.type: Easing.OutQuad
            }
            SequentialAnimation {
                NumberAnimation {
                    target: pulseRingInner
                    property: "opacity"
                    from: 0.0; to: 0.9
                    duration: 150
                }
                NumberAnimation {
                    target: pulseRingInner
                    property: "opacity"
                    from: 0.9; to: 0.0
                    duration: 550
                }
            }
        }
    }

    // =========================================================================
    // 4. TOP DYNAMIC ISLAND
    // =========================================================================
    Rectangle {
        id: dynamicIsland
        anchors.horizontalCenter: parent.horizontalCenter
        readonly property bool isIdle: rootOverlay.isIdle

        // Slide down to 14, or -48 when hidden
        y: islandHidden ? -48 : (islandState === "idle" ? 10 : 14)
        opacity: islandHidden ? 0.0 : 1.0

        Behavior on y {
            NumberAnimation { duration: 320; easing.type: Easing.OutBack }
        }
        Behavior on opacity {
            NumberAnimation { duration: 260; easing.type: Easing.OutCubic }
        }

        // Width & Height & Radius smoothly morph based on active vs idle
        readonly property real targetWidth: rootOverlay.isIdle ?
            (assistModeActive ? 154 : 110) :
            Math.max(220, Math.min(620, contentRow.implicitWidth + 48))

        width: targetWidth
        height: rootOverlay.isIdle ? 30 : 40
        radius: rootOverlay.isIdle ? 15 : 20

        Behavior on width {
            NumberAnimation { duration: 250; easing.type: Easing.OutCubic }
        }
        Behavior on height {
            NumberAnimation { duration: 250; easing.type: Easing.OutCubic }
        }
        Behavior on radius {
            NumberAnimation { duration: 250; easing.type: Easing.OutCubic }
        }

        // Translucent dark glass Obsidian HUD
        color: colDarkGlass
        border.color: colBorderCyan
        border.width: 1.5

        // Subtle glow aura line on bottom edge
        Rectangle {
            anchors.bottom: parent.bottom
            anchors.horizontalCenter: parent.horizontalCenter
            width: parent.width * 0.75
            height: 1
            color: colNeonCyan
            opacity: rootOverlay.isIdle ? 0.3 : 0.7
            Behavior on opacity {
                NumberAnimation { duration: 250; easing.type: Easing.OutCubic }
            }
        }

        // Island Content Row
        Row {
            id: contentRow
            anchors.centerIn: parent
            spacing: rootOverlay.isIdle ? 6 : 10
            Behavior on spacing {
                NumberAnimation { duration: 250; easing.type: Easing.OutCubic }
            }

            // -------------------------------------------------------------
            // Indicator Widget
            // -------------------------------------------------------------
            Item {
                id: indicatorContainer
                width: rootOverlay.isIdle ? 12 : 20
                height: rootOverlay.isIdle ? 12 : 20
                anchors.verticalCenter: parent.verticalCenter
                Behavior on width {
                    NumberAnimation { duration: 250; easing.type: Easing.OutCubic }
                }
                Behavior on height {
                    NumberAnimation { duration: 250; easing.type: Easing.OutCubic }
                }

                // State: "listening" or "speaking" -> 4 animated equalizer bars
                Row {
                    id: eqBars
                    visible: !rootOverlay.isIdle && (islandState === "listening" || islandState === "speaking")
                    anchors.centerIn: parent
                    spacing: 2.5

                    Repeater {
                        model: 4
                        Rectangle {
                            id: eqBar
                            width: 2.5
                            radius: 1.25
                            color: colNeonCyan
                            height: 6

                            SequentialAnimation on height {
                                running: eqBars.visible
                                loops: Animation.Infinite
                                NumberAnimation {
                                    to: (index === 0 ? 16 : index === 1 ? 19 : index === 2 ? 14 : 17)
                                    duration: (index === 0 ? 240 : index === 1 ? 320 : index === 2 ? 280 : 360)
                                    easing.type: Easing.InOutQuad
                                }
                                NumberAnimation {
                                    to: 5
                                    duration: (index === 0 ? 260 : index === 1 ? 300 : index === 2 ? 250 : 330)
                                    easing.type: Easing.InOutQuad
                                }
                            }
                        }
                    }
                }

                // State: "thinking" or "locating" -> rotating dual-ring scanner
                Item {
                    id: scannerRing
                    visible: !rootOverlay.isIdle && (islandState === "thinking" || islandState === "locating")
                    anchors.centerIn: parent
                    width: 18; height: 18

                    Rectangle {
                        anchors.fill: parent
                        radius: 9
                        color: "transparent"
                        border.color: colNeonCyan
                        border.width: 1.5

                        // Small scanner notch
                        Rectangle {
                            x: 7; y: -1
                            width: 4; height: 3
                            radius: 1
                            color: colElectricBlue
                        }
                    }

                    Rectangle {
                        anchors.centerIn: parent
                        width: 8; height: 8; radius: 4
                        color: colDeepBlue

                        SequentialAnimation on scale {
                            running: scannerRing.visible
                            loops: Animation.Infinite
                            NumberAnimation { to: 1.25; duration: 550; easing.type: Easing.InOutQuad }
                            NumberAnimation { to: 0.70; duration: 550; easing.type: Easing.InOutQuad }
                        }
                    }

                    RotationAnimator on rotation {
                        running: scannerRing.visible
                        from: 0; to: 360
                        duration: 1200
                        loops: Animation.Infinite
                    }
                }

                // State: "acting" or "assist" -> glowing pulsing cyan orb
                Item {
                    id: actingOrb
                    visible: !rootOverlay.isIdle && (islandState === "acting" || islandState === "assist")
                    anchors.centerIn: parent
                    width: 18; height: 18

                    Rectangle {
                        anchors.centerIn: parent
                        width: 16; height: 16; radius: 8
                        color: "transparent"
                        border.color: colNeonCyan
                        border.width: 1.5

                        SequentialAnimation on scale {
                            running: actingOrb.visible
                            loops: Animation.Infinite
                            NumberAnimation { to: 1.5; duration: 600; easing.type: Easing.OutQuad }
                            NumberAnimation { to: 0.9; duration: 600; easing.type: Easing.InQuad }
                        }
                        SequentialAnimation on opacity {
                            running: actingOrb.visible
                            loops: Animation.Infinite
                            NumberAnimation { to: 0.2; duration: 600 }
                            NumberAnimation { to: 0.9; duration: 600 }
                        }
                    }

                    Rectangle {
                        anchors.centerIn: parent
                        width: 8; height: 8; radius: 4
                        color: colNeonCyan
                    }
                }

                // State: "idle" -> subtle breathing cyan dot
                Rectangle {
                    id: idleDot
                    visible: rootOverlay.isIdle
                    anchors.centerIn: parent
                    width: 6; height: 6; radius: 3
                    color: colNeonCyan

                    SequentialAnimation on opacity {
                        running: idleDot.visible
                        loops: Animation.Infinite
                        NumberAnimation { to: 0.35; duration: 1100; easing.type: Easing.InOutQuad }
                        NumberAnimation { to: 1.0; duration: 1100; easing.type: Easing.InOutQuad }
                    }
                }
            }

            // -------------------------------------------------------------
            // Text Content
            // -------------------------------------------------------------
            // Idle text: "✦ Aster" (or "✦ Aster • ASSIST")
            Text {
                visible: rootOverlay.isIdle
                anchors.verticalCenter: parent.verticalCenter
                text: assistModeActive ? "✦ Aster • ASSIST" : "✦ Aster"
                color: colTextWhite
                font.pixelSize: 11
                font.bold: true
            }

            // Active text row (statusText + optional subText)
            Row {
                visible: !rootOverlay.isIdle
                anchors.verticalCenter: parent.verticalCenter
                spacing: 8

                Text {
                    id: primaryLabel
                    text: statusText !== "" ? statusText : islandState.toUpperCase()
                    color: colTextWhite
                    font.pixelSize: 12
                    font.bold: true
                    anchors.verticalCenter: parent.verticalCenter
                }

                Rectangle {
                    visible: subText !== ""
                    width: 3; height: 3; radius: 1.5
                    color: colElectricBlue
                    anchors.verticalCenter: parent.verticalCenter
                }

                Text {
                    visible: subText !== ""
                    text: subText
                    color: colTextMuted
                    font.pixelSize: 11
                    anchors.verticalCenter: parent.verticalCenter
                }
            }

            // -------------------------------------------------------------
            // Assist Mode Badge (Shown on right side when assistModeActive is true)
            // -------------------------------------------------------------
            Rectangle {
                visible: !rootOverlay.isIdle && assistModeActive
                anchors.verticalCenter: parent.verticalCenter
                height: 18
                width: badgeText.implicitWidth + 12
                radius: 9
                color: "#3300F0FF"
                border.color: colNeonCyan
                border.width: 1.0

                Text {
                    id: badgeText
                    anchors.centerIn: parent
                    text: "ASSIST"
                    color: colNeonCyan
                    font.pixelSize: 9
                    font.bold: true
                }
            }
        }
    }
}
