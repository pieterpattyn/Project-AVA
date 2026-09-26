import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window

Window {
    id: root

    width: 980
    height: 700
    minimumWidth: 760
    minimumHeight: 560
    visible: true
    color: "#06090f"
    title: "AVA Desktop"

    required property var avaDesktop

    property string avaState: avaDesktop.state
    property string rtxState: avaDesktop.rtxState

    readonly property color cyan: "#7fe9ff"
    readonly property color cyanSoft: "#4aa8ba"
    readonly property color textMain: "#dff9ff"
    readonly property color textMuted: "#718491"
    readonly property color panel: "#0b1119"
    readonly property color panelEdge: "#172a35"

    Rectangle {
        anchors.fill: parent
        color: root.color

        Rectangle {
            anchors.centerIn: parent
            width: Math.min(parent.width * 0.82, 760)
            height: Math.min(parent.height * 0.78, 535)
            radius: 34
            color: root.panel
            border.width: 1
            border.color: root.panelEdge

            Rectangle {
                id: ambientGlow
                anchors.centerIn: parent
                width: 390
                height: 390
                radius: width / 2
                color: "transparent"
                border.width: root.avaState === "listening" ? 3 : 1
                border.color: root.avaState === "listening" ? "#9af3ff" : "#163845"
                opacity: root.rtxState === "off" ? 0.22 : 0.68

                SequentialAnimation on scale {
                    running: root.avaState === "listening"
                    loops: Animation.Infinite
                    NumberAnimation { to: 1.035; duration: 650; easing.type: Easing.InOutSine }
                    NumberAnimation { to: 0.985; duration: 650; easing.type: Easing.InOutSine }
                }
            }

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 26
                spacing: 8

                RowLayout {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 48

                    Text {
                        text: "AVA"
                        color: root.textMain
                        font.pixelSize: 27
                        font.weight: Font.DemiBold
                        font.letterSpacing: 5
                    }

                    Item { Layout.fillWidth: true }

                    Rectangle {
                        implicitWidth: statusRow.implicitWidth + 26
                        implicitHeight: 34
                        radius: 17
                        color: "#0c151d"
                        border.width: 1
                        border.color: root.rtxState === "local"
                                      ? "#245f52"
                                      : (root.rtxState === "partial" ? "#6e5a28" : "#3a4148")

                        Row {
                            id: statusRow
                            anchors.centerIn: parent
                            spacing: 9

                            Rectangle {
                                anchors.verticalCenter: parent.verticalCenter
                                width: 9
                                height: 9
                                radius: 5
                                color: root.rtxState === "local"
                                       ? "#70efbd"
                                       : (root.rtxState === "partial" ? "#ffd36b" : "#76808a")
                            }

                            Text {
                                anchors.verticalCenter: parent.verticalCenter
                                text: avaDesktop.statusText
                                color: root.textMuted
                                font.pixelSize: 12
                                font.letterSpacing: 1
                            }
                        }
                    }
                }

                Item {
                    id: faceStage
                    Layout.fillWidth: true
                    Layout.fillHeight: true

                    Item {
                        id: face
                        width: Math.min(parent.width * 0.72, 510)
                        height: 255
                        anchors.centerIn: parent
                        opacity: root.rtxState === "off" ? 0.38 : 1.0

                        property real eyeHeight: root.avaState === "thinking" ? 12 : 34
                        property color eyeColor: root.avaState === "listening"
                                                 ? "#effdff"
                                                 : root.cyan

                        Rectangle {
                            id: leftEye
                            width: 116
                            height: face.eyeHeight
                            radius: height / 2
                            anchors.left: parent.left
                            anchors.leftMargin: 64
                            anchors.verticalCenter: parent.verticalCenter
                            color: face.eyeColor

                            Behavior on height { NumberAnimation { duration: 180 } }
                            Behavior on color { ColorAnimation { duration: 180 } }
                        }

                        Rectangle {
                            id: rightEye
                            width: 116
                            height: face.eyeHeight
                            radius: height / 2
                            anchors.right: parent.right
                            anchors.rightMargin: 64
                            anchors.verticalCenter: parent.verticalCenter
                            color: face.eyeColor

                            Behavior on height { NumberAnimation { duration: 180 } }
                            Behavior on color { ColorAnimation { duration: 180 } }
                        }

                        Rectangle {
                            id: mouth
                            width: root.avaState === "speaking" ? 92 : 56
                            height: 7
                            radius: height / 2
                            anchors.horizontalCenter: parent.horizontalCenter
                            anchors.bottom: parent.bottom
                            anchors.bottomMargin: 8
                            color: root.cyan

                            SequentialAnimation {
                                running: root.avaState === "speaking"
                                loops: Animation.Infinite

                                NumberAnimation {
                                    target: mouth
                                    property: "height"
                                    to: 27
                                    duration: 115
                                }

                                NumberAnimation {
                                    target: mouth
                                    property: "height"
                                    to: 7
                                    duration: 145
                                }
                            }
                        }

                        SequentialAnimation {
                            id: blinkAnimation
                            running: root.avaState === "idle" && root.rtxState !== "off"
                            loops: Animation.Infinite

                            PauseAnimation { duration: 4200 }
                            ParallelAnimation {
                                NumberAnimation { target: leftEye; property: "height"; to: 5; duration: 80 }
                                NumberAnimation { target: rightEye; property: "height"; to: 5; duration: 80 }
                            }
                            ParallelAnimation {
                                NumberAnimation { target: leftEye; property: "height"; to: 34; duration: 110 }
                                NumberAnimation { target: rightEye; property: "height"; to: 34; duration: 110 }
                            }
                        }
                    }

                    Text {
                        anchors.horizontalCenter: parent.horizontalCenter
                        anchors.bottom: parent.bottom
                        anchors.bottomMargin: 12
                        text: root.rtxState === "off"
                              ? "SLAPEND"
                              : root.avaState.toUpperCase()
                        color: root.textMuted
                        font.pixelSize: 13
                        font.letterSpacing: 4
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 76
                    radius: 20
                    color: "#091018"
                    border.width: 1
                    border.color: "#152733"

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: 18
                        anchors.rightMargin: 18
                        spacing: 12

                        Button {
                            id: talkButton
                            Layout.preferredWidth: 150
                            Layout.preferredHeight: 44
                            text: root.avaState === "listening" ? "Luistert..." : "Praat"

                            enabled: root.rtxState === "local" && !avaDesktop.busy

                            onClicked: avaDesktop.demoListening()

                            background: Rectangle {
                                radius: 14
                                color: talkButton.enabled
                                       ? (talkButton.down ? "#143746" : "#102c38")
                                       : "#10161c"
                                border.width: 1
                                border.color: talkButton.enabled ? "#3f93a8" : "#273039"
                            }

                            contentItem: Text {
                                text: talkButton.text
                                color: talkButton.enabled ? root.textMain : "#59636b"
                                font.pixelSize: 14
                                horizontalAlignment: Text.AlignHCenter
                                verticalAlignment: Text.AlignVCenter
                            }
                        }

                        Text {
                            Layout.fillWidth: true
                            text: root.rtxState === "local"
                                  ? "Lokale AI beschikbaar"
                                  : "AVA blijft zichtbaar; lokaal brein is uitgeschakeld"
                            color: root.textMuted
                            font.pixelSize: 13
                            elide: Text.ElideRight
                        }

                        Button {
                            id: powerButton
                            Layout.preferredWidth: 150
                            Layout.preferredHeight: 44

                            text: avaDesktop.busy
                                  ? "Even..."
                                  : (root.rtxState === "local" || root.rtxState === "partial"
                                     ? "RTX AI uit"
                                     : "RTX AI aan")

                            enabled: !avaDesktop.busy

                            onClicked: avaDesktop.toggleRtx()

                            background: Rectangle {
                                radius: 14
                                color: powerButton.down ? "#242b33" : "#151d25"
                                border.width: 1
                                border.color: root.rtxState === "local" ? "#38695e" : "#39444d"
                            }

                            contentItem: Text {
                                text: powerButton.text
                                color: root.rtxState === "local" ? "#9ce8d2" : root.textMain
                                font.pixelSize: 14
                                horizontalAlignment: Text.AlignHCenter
                                verticalAlignment: Text.AlignVCenter
                            }
                        }
                    }
                }

                Text {
                    Layout.fillWidth: true
                    visible: avaDesktop.errorText.length > 0
                    text: avaDesktop.errorText
                    color: "#ff9a94"
                    font.pixelSize: 12
                    wrapMode: Text.Wrap
                }
            }
        }

        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.bottom: parent.bottom
            anchors.bottomMargin: 15
            text: "PROJECT AVA · DESKTOP 0.1"
            color: "#35444e"
            font.pixelSize: 10
            font.letterSpacing: 2
        }
    }

    Shortcut {
        sequence: "Escape"
        onActivated: Qt.quit()
    }
}
