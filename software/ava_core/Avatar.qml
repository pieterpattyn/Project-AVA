import QtQuick
import QtQuick.Window

Window {
    id: root
    visible: true
    visibility: Window.FullScreen
    color: "#080b10"
    title: "Project AVA"

    property string avaState: "idle"

    Rectangle {
        anchors.fill: parent
        color: root.color

        Text {
            id: title
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.top: parent.top
            anchors.topMargin: 35
            text: "AVA"
            color: "#b8f7ff"
            font.pixelSize: 38
            font.bold: true
        }

        Item {
            id: face
            width: Math.min(parent.width * 0.72, 520)
            height: 220
            anchors.centerIn: parent

            Rectangle {
                id: leftEye
                width: 105
                height: root.avaState === "thinking" ? 16 : 42
                radius: height / 2
                anchors.left: parent.left
                anchors.leftMargin: 65
                anchors.verticalCenter: parent.verticalCenter
                color: root.avaState === "listening" ? "#ffffff" : "#8defff"

                Behavior on height {
                    NumberAnimation { duration: 180 }
                }
            }

            Rectangle {
                id: rightEye
                width: 105
                height: root.avaState === "thinking" ? 16 : 42
                radius: height / 2
                anchors.right: parent.right
                anchors.rightMargin: 65
                anchors.verticalCenter: parent.verticalCenter
                color: root.avaState === "listening" ? "#ffffff" : "#8defff"

                Behavior on height {
                    NumberAnimation { duration: 180 }
                }
            }

            Rectangle {
                id: mouth
                width: root.avaState === "speaking" ? 95 : 55
                height: root.avaState === "speaking" ? 22 : 8
                radius: height / 2
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.bottom: parent.bottom
                color: "#8defff"

                SequentialAnimation on height {
                    running: root.avaState === "speaking"
                    loops: Animation.Infinite
                    NumberAnimation { to: 28; duration: 120 }
                    NumberAnimation { to: 8; duration: 140 }
                }
            }
        }

        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.bottom: parent.bottom
            anchors.bottomMargin: 35
            text: root.avaState.toUpperCase()
            color: "#71808f"
            font.pixelSize: 20
            font.letterSpacing: 4
        }
    }

    Shortcut {
        sequence: "Escape"
        onActivated: Qt.quit()
    }
}
