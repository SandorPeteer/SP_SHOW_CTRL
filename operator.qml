import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Window

ApplicationWindow {
    id: screen
    title: "S.P. Show Control"
    width: Math.max(minimumWidth, Math.min(1400, Screen.desktopAvailableWidth - 24))
    height: Math.max(minimumHeight, Math.min(820, Screen.desktopAvailableHeight - 36))
    minimumWidth: 900
    minimumHeight: 550
    visible: true
    flags: Qt.Window | Qt.FramelessWindowHint
    color: "#0d131b"

    readonly property var ui: operatorBridge.state
    readonly property color panel: "#121d28"
    readonly property color field: "#0e1722"
    readonly property color edge: "#2b3949"
    readonly property color ink: "#ecf5ff"
    readonly property color quiet: "#a9bbcd"
    readonly property color cyan: "#59d8ff"
    readonly property color mint: "#57f3b9"
    readonly property color violet: "#c3a3ff"
    readonly property color amber: "#ffd27c"
    readonly property color coral: "#ff748b"

    onClosing: function(close) {
        close.accepted = false
        operatorBridge.press("close", null)
    }
    function action(name) { operatorBridge.press(name, null) }
    function toggleWindow() { if (visibility === Window.Maximized) showNormal(); else showMaximized() }

    Shortcut { sequence: StandardKey.Open; onActivated: screen.action("open") }
    Shortcut { sequence: StandardKey.Save; onActivated: screen.action("save") }
    Shortcut { sequence: "PageUp"; enabled: !!screen.ui.slides; onActivated: operatorBridge.press("slide_key", -1) }
    Shortcut { sequence: "PageDown"; enabled: !!screen.ui.slides; onActivated: operatorBridge.press("slide_key", 1) }
    Shortcut { sequence: "Left"; enabled: !!screen.ui.slides; onActivated: operatorBridge.press("slide_key", -1) }
    Shortcut { sequence: "Right"; enabled: !!screen.ui.slides; onActivated: operatorBridge.press("slide_key", 1) }

    component Glyph: Image {
        property string symbol
        property color tone: screen.ink
        source: symbol ? "image://icon/" + symbol + "/" + tone.toString().slice(1) : ""
        sourceSize: Qt.size(Math.ceil(width * screen.Screen.devicePixelRatio), Math.ceil(height * screen.Screen.devicePixelRatio))
        fillMode: Image.PreserveAspectFit
    }

    component Caption: Text {
        color: screen.quiet
        font.pixelSize: 12
        elide: Text.ElideRight
        verticalAlignment: Text.AlignVCenter
        textFormat: Text.PlainText
    }

    component KeyButton: Button {
        id: key
        property string symbol
        property string label
        property string command
        property bool showLabel: false
        property color tone: screen.ink
        property color base: "#1c2938"
        property color line: screen.edge
        text: label
        hoverEnabled: true
        implicitWidth: showLabel ? labelText.implicitWidth + (symbol ? 40 : 23) : 28
        implicitHeight: 30
        onClicked: if (command) screen.action(command)
        ToolTip.visible: hovered && label !== "" && !showLabel
        ToolTip.delay: 450
        ToolTip.text: label
        background: Rectangle {
            radius: 5
            color: key.down ? Qt.darker(key.base, 1.20) : key.hovered ? Qt.lighter(key.base, 1.23) : key.base
            border.color: key.visualFocus ? screen.cyan : key.line
        }
        contentItem: Item {
            Row {
                anchors.centerIn: parent
                spacing: 5
                Glyph { width: 17; height: 17; visible: key.symbol !== ""; symbol: key.symbol; tone: key.tone }
                Text { id: labelText; visible: key.showLabel; text: key.label; color: key.tone; font.pixelSize: 13; font.weight: Font.DemiBold }
            }
        }
    }

    component Tile: Rectangle {
        color: screen.panel
        border.color: screen.edge
        radius: 6
        antialiasing: true
    }

    component PreviewTile: Tile {
        property bool live
        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 7
            spacing: 5
            RowLayout {
                Layout.fillWidth: true
                Glyph { width: 16; height: 16; symbol: live ? "screen" : "slides"; tone: live ? screen.mint : screen.violet }
                Caption {
                    color: screen.ink
                    font.weight: Font.DemiBold
                    text: live ? "On screen" : screen.ui.slides ? "Next slide" : "Preview"
                    Layout.fillWidth: true
                }
                Rectangle {
                    visible: live && !!screen.ui.projectionClock && screen.ui.projectionClock.fraction >= 0
                    width: 49; height: 4; radius: 2; color: "#284256"
                    Rectangle {
                        width: parent.width * (screen.ui.projectionClock ? screen.ui.projectionClock.fraction : 0)
                        height: parent.height; radius: 2; color: screen.mint
                        Behavior on width { NumberAnimation { duration: 34 } }
                    }
                }
                Caption {
                    visible: live && (!!operatorBridge.previews.live || !!(screen.ui.projectionClock && screen.ui.projectionClock.label))
                    color: screen.mint
                    text: screen.ui.projectionClock && screen.ui.projectionClock.label
                          ? screen.ui.projectionClock.label : "● Live"
                }
            }
            Rectangle {
                color: "#080e16"
                clip: true
                Layout.fillWidth: true
                Layout.fillHeight: true
                Image {
                    anchors.fill: parent
                    source: (live ? operatorBridge.previews.live : operatorBridge.previews.next) || ""
                    fillMode: Image.PreserveAspectFit
                    cache: false
                    smooth: true
                }
            }
            Caption {
                Layout.fillWidth: true
                text: live ? (screen.ui.liveCaption || "") : (screen.ui.nextCaption || "")
                height: 17
            }
        }
    }

    component WaveTile: Tile {
        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 7
            spacing: 5
            RowLayout {
                Layout.fillWidth: true
                Glyph { width: 16; height: 16; symbol: "waveform"; tone: screen.cyan }
                Caption { color: screen.ink; font.weight: Font.DemiBold; text: "Waveform"; Layout.fillWidth: true }
                Caption { color: screen.cyan; font.pixelSize: 14; font.weight: Font.DemiBold; text: screen.ui.time || "—" }
                Caption { text: screen.ui.duration || "" }
            }
            Rectangle {
                id: plot
                color: screen.field
                clip: true
                Layout.fillWidth: true
                Layout.fillHeight: true
                readonly property var bars: operatorBridge.waveform
                Item {
                    id: waveformGeometry
                    width: 512
                    height: 160
                    transform: Scale {
                        xScale: plot.width / waveformGeometry.width
                        yScale: plot.height / waveformGeometry.height
                    }
                    Repeater {
                        model: plot.bars
                        delegate: Rectangle {
                            required property var modelData
                            required property int index
                            x: index * waveformGeometry.width / plot.bars.length
                            width: waveformGeometry.width * 0.65 / plot.bars.length
                            height: Math.max(1, waveformGeometry.height * modelData.height)
                            y: (waveformGeometry.height - height) / 2
                            color: modelData.color
                        }
                    }
                }
                Rectangle {
                    visible: !!screen.ui.inPoint
                    x: (screen.ui.inPoint || 0) * parent.width; width: 1; height: parent.height; color: screen.cyan
                    Caption { x: 3; y: 2; text: "IN"; color: screen.cyan }
                }
                Rectangle {
                    visible: !!screen.ui.outPoint
                    x: (screen.ui.outPoint || 0) * parent.width - 1; width: 1; height: parent.height; color: screen.violet
                    Caption { anchors.right: parent.left; anchors.rightMargin: 3; y: 2; text: "OUT"; color: screen.violet }
                }
                Rectangle {
                    visible: screen.ui.cursor >= 0
                    x: Math.max(0, screen.ui.cursor || 0) * parent.width
                    width: 1; height: parent.height; color: screen.ink
                    Behavior on x { NumberAnimation { duration: 34 } }
                }
                Caption {
                    anchors.centerIn: parent
                    visible: !!screen.ui.waveLoading
                    text: "Analyzing audio…"
                }
                TapHandler {
                    onTapped: function(point) {
                        operatorBridge.press("seek", point.position.x / plot.width)
                    }
                }
                TapHandler {
                    acceptedButtons: Qt.RightButton | Qt.MiddleButton
                    onTapped: function(point) {
                        operatorBridge.press("mark_out", point.position.x / plot.width)
                    }
                }
            }
            RowLayout {
                Layout.fillWidth: true
                Repeater {
                    model: screen.ui.ticks || []
                    Caption {
                        required property string modelData
                        required property int index
                        Layout.fillWidth: true
                        horizontalAlignment: index === 0 ? Text.AlignLeft : index === 4 ? Text.AlignRight : Text.AlignHCenter
                        text: modelData
                    }
                }
            }
            Caption { Layout.fillWidth: true; text: screen.ui.source || "" }
        }
    }

    component CuePanel: Tile {
        id: card
        required property string heading
        required property string deck
        required property var rows
        required property var editButtons
        required property Component transport
        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 7
            spacing: 7
            RowLayout {
                Layout.fillWidth: true
                Caption { text: heading; color: screen.ink; font.weight: Font.DemiBold; Layout.fillWidth: true }
                Repeater {
                    model: card.editButtons
                    KeyButton {
                        required property var modelData
                        symbol: modelData[0]; label: modelData[1]; command: modelData[2]
                        implicitWidth: 25; implicitHeight: 25
                    }
                }
            }
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                color: screen.field
                radius: 4
                ListView {
                    id: list
                    anchors.fill: parent
                    clip: true
                    model: card.rows
                    boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: ScrollBar {
                        width: 5
                        opacity: list.moving ? 1 : 0
                        Behavior on opacity { NumberAnimation { duration: 170 } }
                        interactive: false
                        contentItem: Rectangle { implicitWidth: 3; radius: 2; color: "#5a7287" }
                    }
                    delegate: Rectangle {
                        id: row
                        required property var modelData
                        required property int index
                        width: ListView.view.width
                        height: Math.max(34, rowName.implicitHeight + 13)
                        readonly property color accent: modelData.role === "playing" ? screen.mint :
                              modelData.role === "next" ? screen.violet : modelData.selected ? screen.cyan : "transparent"
                        color: modelData.role === "playing" ? "#15382f" : modelData.selected ? "#193747" :
                               modelData.role === "next" ? "#2b2541" : screen.field
                        Rectangle { height: parent.height; width: 2; color: row.accent }
                        Rectangle { anchors.bottom: parent.bottom; height: 1; width: parent.width; color: "#1f2c3c" }
                        MouseArea {
                            anchors.fill: parent
                            onClicked: operatorBridge.press(card.deck === "scene" ? "scene" : card.deck + "_select", row.index)
                            onDoubleClicked: if (card.deck !== "scene") screen.action(card.deck + "_play")
                        }
                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 7
                            anchors.rightMargin: 9
                            spacing: 7
                            Caption { visible: card.deck === "scene"; text: row.modelData.number || ""; Layout.preferredWidth: 17 }
                            Item {
                                visible: card.deck !== "scene"
                                Layout.preferredWidth: 19
                                Layout.fillHeight: true
                                Rectangle {
                                    anchors.centerIn: parent
                                    visible: !!row.modelData.armed || !!row.modelData.checkbox
                                    width: 16; height: 16; radius: 3; color: "#17392f"
                                    Caption {
                                        anchors.centerIn: parent
                                        text: row.modelData.checkbox ? (row.modelData.checked ? "✓" : "□") : "A"
                                        color: screen.mint
                                        font.weight: Font.DemiBold
                                    }
                                }
                                MouseArea {
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    onClicked: operatorBridge.press(card.deck + (row.modelData.checkbox ? "_check" : "_auto"), row.index)
                                    ToolTip.visible: containsMouse
                                    ToolTip.delay: 550
                                    ToolTip.text: row.modelData.checkbox ? "Select for removal" : "Toggle autoplay"
                                }
                            }
                            Glyph {
                                visible: card.deck !== "scene"
                                symbol: row.modelData.kind || ""; Layout.preferredWidth: 16; Layout.preferredHeight: 16
                            }
                            Caption {
                                id: rowName
                                Layout.fillWidth: true
                                color: screen.ink
                                text: row.modelData.name
                                wrapMode: card.deck === "scene" ? Text.Wrap : Text.NoWrap
                                elide: card.deck === "scene" ? Text.ElideNone : Text.ElideRight
                                font.pixelSize: 13
                            }
                            Caption { visible: card.deck !== "scene"; text: row.modelData.detail || "" }
                        }
                    }
                }
                DropArea {
                    anchors.fill: parent
                    enabled: card.deck !== "scene"
                    onDropped: function(drop) { if (drop.hasUrls) operatorBridge.drop(card.deck, drop.urls) }
                }
            }
            Loader { sourceComponent: card.transport; Layout.fillWidth: true }
        }
    }

    ColumnLayout {
        id: desk
        anchors.fill: parent
        anchors.margins: 10
        spacing: 8
        RowLayout {
            Layout.fillWidth: true
            spacing: 5
            Caption { text: "SP Show Control"; color: screen.ink; font.pixelSize: 14; font.weight: Font.DemiBold; Layout.rightMargin: 5 }
            KeyButton {
                id: fileButton
                label: "File ▾"; showLabel: true
                onClicked: fileMenu.open()
                Menu {
                    id: fileMenu
                    y: fileButton.height
                    MenuItem { text: "New show"; onTriggered: screen.action("new") }
                    MenuItem { text: "Save show as…"; onTriggered: screen.action("save_as") }
                }
            }
            KeyButton { symbol: "open"; label: "Open show"; command: "open" }
            KeyButton { symbol: "save"; label: "Save show"; command: "save" }
            Item {
                Layout.fillWidth: true
                Layout.preferredHeight: 30
                Caption { anchors.fill: parent; anchors.leftMargin: 7; text: screen.ui.show || "" }
                DragHandler {
                    target: null
                    onActiveChanged: if (active) screen.startSystemMove()
                }
                TapHandler { onDoubleTapped: screen.toggleWindow() }
            }
            RowLayout {
                visible: !!screen.ui.activity
                Caption { text: screen.ui.activity || ""; color: screen.cyan; Layout.maximumWidth: Math.max(70, screen.width / 6) }
                BusyIndicator { visible: !!screen.ui.busy; running: visible; implicitWidth: 20; implicitHeight: 20 }
            }
            KeyButton { symbol: "fullscreen"; label: screen.ui.fullscreen || "Fullscreen"; showLabel: true; command: "fullscreen"; base: screen.cyan; line: screen.cyan; tone: "#07131d" }
            KeyButton { symbol: "settings"; label: "App settings"; command: "settings" }
            KeyButton { symbol: "minimize"; label: "Minimize"; base: screen.color; line: screen.color; onClicked: screen.showMinimized() }
            KeyButton { symbol: "maximize"; label: "Maximize / restore"; base: screen.color; line: screen.color; onClicked: screen.toggleWindow() }
            KeyButton { symbol: "close"; label: "Close"; base: screen.color; line: screen.color; onClicked: screen.action("close") }
        }
        Item {
            id: workspace
            Layout.fillWidth: true
            Layout.fillHeight: true
            Item {
                id: topRow
                width: parent.width
                height: Math.floor((workspace.height - 8) / 3)
                readonly property real available: width - 16
                WaveTile { width: topRow.available * 0.40; height: topRow.height }
                PreviewTile { x: topRow.available * 0.40 + 8; width: topRow.available * 0.30; height: topRow.height; live: true }
                PreviewTile { x: topRow.available * 0.70 + 16; width: topRow.available * 0.30; height: topRow.height; live: false }
            }
            Item {
                id: lower
                width: parent.width
                y: topRow.height + 8
                height: parent.height - y
                readonly property real available: width - 16
                CuePanel {
                    heading: "Rundown"; deck: "scene"; rows: operatorBridge.lists.scenes || []
                    width: lower.available * 0.24; height: lower.height
                    editButtons: [["add","Add scene","scene_add"],["remove","Remove scene","scene_remove"],
                                  ["up","Move scene up","scene_up"],["down","Move scene down","scene_down"]]
                    transport: RowLayout {
                        KeyButton { symbol: "previous"; label: "Previous scene"; command: "scene_prev" }
                        KeyButton { symbol: "next"; label: "Next scene"; command: "scene_next" }
                        Item { Layout.fillWidth: true }
                        KeyButton { symbol: "edit"; label: "Edit scene"; command: "scene_edit" }
                    }
                }
                CuePanel {
                    heading: "Audio / Video"; deck: "a"; rows: operatorBridge.lists.audio || []
                    x: lower.available * 0.24 + 8; width: lower.available * 0.34; height: lower.height
                    editButtons: [["add","Add media","a_add"],["remove","Remove media","a_remove"],
                                  ["up","Move item up","a_up"],["down","Move item down","a_down"],["settings","Media tools","a_tools"]]
                    transport: RowLayout {
                        KeyButton { symbol: screen.ui.play === "Pause" ? "pause" : "play"; label: screen.ui.play || "Play"; showLabel: true; command: "a_play"; base: screen.ui.play === "Pause" ? screen.amber : screen.cyan; line: base; tone: "#07131d" }
                        KeyButton { symbol: "stop"; label: "Stop media"; command: "a_stop"; tone: screen.coral; base: "#331e2a"; line: "#754559" }
                        KeyButton { symbol: "repeat"; label: "Repeat media"; command: "a_repeat"; tone: screen.ui.repeatA ? screen.violet : screen.ink }
                        Item { Layout.fillWidth: true }
                    }
                }
                CuePanel {
                    heading: "Projection"; deck: "b"; rows: operatorBridge.lists.projection || []
                    x: lower.available * 0.58 + 16; width: lower.available * 0.42; height: lower.height
                    editButtons: [["add","Add visuals","b_add"],["remove","Remove visuals","b_remove"],
                                  ["up","Move item up","b_up"],["down","Move item down","b_down"],
                                  ["download","Downloads","download"],["settings","Projection tools","b_tools"]]
                    transport: RowLayout {
                        KeyButton { symbol: "show"; label: "Show"; showLabel: true; command: "b_play"; base: screen.mint; line: screen.mint; tone: "#07131d" }
                        KeyButton { symbol: "previous"; label: "Previous slide"; command: "slide_prev"; tone: screen.violet }
                        Caption { text: screen.ui.slidePosition || "— / —"; color: screen.ink }
                        KeyButton { symbol: "next"; label: "Next slide"; command: "slide_next"; tone: screen.violet }
                        KeyButton { symbol: "stop"; label: "Clear projection"; command: "b_stop"; tone: screen.coral; base: "#331e2a"; line: "#754559" }
                        KeyButton { symbol: "repeat"; label: "Repeat projection"; command: "b_repeat"; tone: screen.ui.repeatB ? screen.violet : screen.ink }
                        Item { Layout.fillWidth: true }
                    }
                }
            }
        }
    }

    Repeater {
        model: [Qt.LeftEdge, Qt.RightEdge, Qt.TopEdge, Qt.BottomEdge,
                Qt.LeftEdge | Qt.TopEdge, Qt.RightEdge | Qt.TopEdge,
                Qt.LeftEdge | Qt.BottomEdge, Qt.RightEdge | Qt.BottomEdge]
        MouseArea {
            required property int modelData
            readonly property bool leftEdge: (modelData & Qt.LeftEdge) !== 0
            readonly property bool rightEdge: (modelData & Qt.RightEdge) !== 0
            readonly property bool topEdge: (modelData & Qt.TopEdge) !== 0
            readonly property bool bottomEdge: (modelData & Qt.BottomEdge) !== 0
            width: leftEdge || rightEdge ? 6 : screen.width
            height: topEdge || bottomEdge ? 6 : screen.height
            x: rightEdge ? screen.width - width : 0
            y: bottomEdge ? screen.height - height : 0
            z: 5
            visible: screen.visibility !== Window.Maximized
            cursorShape: leftEdge && topEdge || rightEdge && bottomEdge ? Qt.SizeFDiagCursor :
                         rightEdge && topEdge || leftEdge && bottomEdge ? Qt.SizeBDiagCursor :
                         leftEdge || rightEdge ? Qt.SizeHorCursor : Qt.SizeVerCursor
            onPressed: screen.startSystemResize(modelData)
        }
    }
}
