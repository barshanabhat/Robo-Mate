import json
import os
import sys
import random
from datetime import datetime
from math import hypot

from PySide6.QtCore import Qt, QPoint, QTimer
from PySide6.QtGui import QPainter, QPen, QColor, QLinearGradient
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QDialog,
    QInputDialog,
    QMenu,
    QWidget
)

from database import RoboMateDatabase
from chat_dialog import ChatDialog


class RoboMateWindow(QWidget):
    def __init__(self):
        super().__init__()

        self.database = RoboMateDatabase()
        self.database.initialize_database()
        self.current_project = self.database.get_latest_project()
        self.chat_dialog = None
        self.is_shutting_down = False

        self.settings_file = "settings.json"

        self.walk_frame = 0

        self.drag_position = QPoint()
        self.press_position = QPoint()
        self.press_click_position = QPoint()
        self.left_press_active = False
        self.is_dragging = False
        self.eyes_closed = False
        self.expression = "normal"
        self.is_waving = False
        self.wave_angle = 0
        self.wave_direction = 1
        self.is_sleepy = False
        self.idle_animation_tick = 0

        # Walking settings
        self.is_walking = False
        self.roaming_enabled = True
        self.walk_direction = 1
        self.walk_speed = 4
        self.target_x = 0
        self.target_y = 0

        self.walk_timer = QTimer(self)
        self.walk_timer.timeout.connect(self.update_walk)
        self.walk_timer.start(30)

        self.idle_wait_timer = QTimer(self)
        self.idle_wait_timer.setSingleShot(True)
        self.idle_wait_timer.timeout.connect(self.start_roaming_walk)

        self.wave_timer = QTimer(self)
        self.wave_timer.setInterval(50)
        self.wave_timer.timeout.connect(self.update_wave)

        self.inactivity_timer = QTimer(self)
        self.inactivity_timer.setSingleShot(True)
        self.inactivity_timer.timeout.connect(self.enter_sleepy_state)

        # Idle animation values
        self.float_offset = 0
        self.float_direction = 1
        self.idle_pose = "normal"
        self.idle_pose_step = 0
        self.gaze_offset = 0.0
        self.head_tilt = 0.0
        self.target_gaze_offset = 0.0
        self.target_head_tilt = 0.0
        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint |
            Qt.Tool
        )

        self.setAttribute(Qt.WA_TranslucentBackground)

        self.resize(260, 300)

        # Message bubble
        self.message_label = QLabel("", self)
        self.message_label.setAlignment(Qt.AlignCenter)
        self.message_label.setGeometry(27, 5, 205, 45)
        self.message_label.setWordWrap(True)
        self.message_label.setAttribute(Qt.WA_TransparentForMouseEvents)

        self.message_label.setStyleSheet("""
            QLabel {
                background-color: white;
                color: black;
                border: 2px solid #00dfff;
                border-radius: 15px;
                padding: 3px;
                font-size: 13px;
            }
        """)
        self.message_label.hide()
        self.message_id = 0

        # Blink timer
        self.blink_timer = QTimer(self)
        self.blink_timer.timeout.connect(self.start_blink)
        self.blink_timer.start(3000)

        # Idle floating animation timer
        self.idle_timer = QTimer(self)
        self.idle_timer.timeout.connect(self.update_idle_animation)
        self.idle_timer.start(60)

        # Occasionally change the robot's gaze or head angle.
        self.idle_pose_timer = QTimer(self)
        self.idle_pose_timer.timeout.connect(self.update_idle_pose)
        self.idle_pose_timer.start(1500)

        self.reset_inactivity_timer()
        self.load_settings()
        QTimer.singleShot(
            500,
            self.show_startup_greeting
        )

    def load_settings(self):
        if not os.path.exists(self.settings_file):
            return

        try:
            with open(self.settings_file, "r") as file:
                settings = json.load(file)

            x = settings.get("x")
            y = settings.get("y")

            if x is not None and y is not None:
                self.move(x, y)

            roaming_enabled = settings.get("roaming_enabled", True)
            self.roaming_enabled = roaming_enabled

        except Exception as error:
            print("Could not load settings:", error)

    def save_settings(self):
        settings = {
            "x": self.x(),
            "y": self.y(),
            "roaming_enabled": self.roaming_enabled
        }

        try:
            with open(self.settings_file, "w") as file:
                json.dump(settings, file, indent=4)

        except Exception as error:
            print("Could not save settings:", error)

    def closeEvent(self, event):
        self.save_settings()
        event.accept()

    def quit_robomate(self):
        if self.is_shutting_down:
            return

        self.is_shutting_down = True
        self.save_settings()

        timer_names = (
            "walk_timer",
            "idle_wait_timer",
            "wave_timer",
            "inactivity_timer",
            "blink_timer",
            "idle_timer",
            "idle_pose_timer"
        )
        for timer_name in timer_names:
            timer = getattr(self, timer_name, None)
            if timer is not None:
                timer.stop()

        if self.chat_dialog is not None:
            response_worker = self.chat_dialog.response_worker
            if response_worker is not None and response_worker.isRunning():
                response_worker.wait()

            self.chat_dialog.close()
            self.chat_dialog = None

        self.close()

        application = QApplication.instance()
        if application is not None:
            application.quit()

    def reset_inactivity_timer(self):
        self.inactivity_timer.start(120000)

    def register_interaction(self):
        self.reset_inactivity_timer()

        if not self.is_sleepy:
            return

        self.is_sleepy = False
        self.expression = "normal"
        self.eyes_closed = False
        self.float_direction = 1
        self.show_message("Hey! You're back 👋", duration=3000)
        self.update()

        if self.roaming_enabled:
            print("Resuming roaming after wake-up")
            self.start_roaming_walk()

    def enter_sleepy_state(self):
        if self.is_sleepy:
            return

        print("Entering sleepy state")
        self.is_sleepy = True
        self.expression = "sleepy"
        self.idle_wait_timer.stop()
        self.is_walking = False
        self.walk_frame = 0
        self.update()

    def get_startup_greeting(self):
        current_hour = datetime.now().hour

        if 5 <= current_hour < 12:
            return "Good morning! ☀️", "Ready to build something?"
        if 12 <= current_hour < 17:
            return "Good afternoon! 🤖", "Ready to continue?"
        if 17 <= current_hour < 22:
            return "Good evening! 🌙", "Let's get something done."
        return "You're still up? 🌙", "I'm here if you want to keep going."

    def show_startup_greeting(self):
        first_message, second_message = self.get_startup_greeting()
        self.show_message(first_message, duration=2000)
        self.start_wave()
        QTimer.singleShot(
            2000,
            lambda: self.show_message(second_message, duration=4000)
        )
        QTimer.singleShot(6500, self.show_latest_project_message)
        print("Startup roaming scheduled")
        QTimer.singleShot(6000, self.start_startup_roaming)

    def show_latest_project_message(self):
        if self.current_project is not None:
            project_name = self.current_project[1]
            self.show_message(f"Last project: {project_name}", duration=3000)

    def start_startup_roaming(self):
        if self.roaming_enabled:
            self.start_roaming_walk()

    def start_wave(self):
        self.is_waving = True
        self.wave_angle = 0
        self.wave_direction = 1
        self.wave_timer.start()
        QTimer.singleShot(1800, self.stop_wave)
        self.update()

    def update_wave(self):
        self.wave_angle += 5 * self.wave_direction

        if self.wave_angle >= 15 or self.wave_angle <= -15:
            self.wave_direction *= -1

        self.update()

    def stop_wave(self):
        self.wave_timer.stop()
        self.is_waving = False
        self.wave_angle = 0
        self.update()

    def show_message(self, text, duration=5000):
        self.message_id += 1
        message_id = self.message_id
        self.message_label.setText(text)
        self.message_label.show()
        QTimer.singleShot(duration, lambda: self.hide_message(message_id))

    def hide_message(self, message_id):
        if message_id == self.message_id:
            self.message_label.hide()

    def pause_roaming(self):
        print("Pausing roaming")
        self.roaming_enabled = False
        self.idle_wait_timer.stop()
        self.is_walking = False
        self.walk_frame = 0
        self.update()

    def resume_roaming(self):
        self.roaming_enabled = True
        self.idle_wait_timer.stop()
        if not self.is_sleepy:
            print("Resuming roaming")
            self.start_roaming_walk()

    def schedule_next_roam(self, delay):
        self.idle_wait_timer.stop()
        if self.roaming_enabled and not self.is_sleepy:
            print(f"Scheduling next roam in {delay} ms")
            self.idle_wait_timer.start(delay)

    def show_project_selector(self):
        projects = self.database.get_projects()
        if not projects:
            self.show_message("No saved projects yet.")
            return

        menu = QMenu(self)
        for project in projects:
            action = menu.addAction(project[1])
            action.setData(project[0])

        selected_action = menu.exec(self.mapToGlobal(self.rect().center()))
        if selected_action is None:
            return

        project_id = selected_action.data()
        self.current_project = self.database.get_project_by_id(project_id)
        if self.current_project is not None:
            self.show_message(f"Working on: {self.current_project[1]}")

    def ask_project_text(self, label):
        dialog = QInputDialog(self)
        dialog.setWindowTitle("Add Project")
        dialog.setLabelText(label)
        dialog.setInputMode(QInputDialog.TextInput)
        dialog.setWindowFlags(
            Qt.Dialog |
            Qt.WindowStaysOnTopHint
        )
        dialog.adjustSize()
        dialog.move(
            self.mapToGlobal(self.rect().center())
            - dialog.rect().center()
        )

        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        return dialog.textValue(), accepted

    def add_new_project(self):
        project_name, accepted = self.ask_project_text("Project name:")
        if not accepted:
            return

        project_name = project_name.strip()
        if not project_name:
            return

        if self.database.get_project_by_name(project_name) is not None:
            self.show_message("That project already exists.")
            return

        folder_path, accepted = self.ask_project_text("Project folder path:")
        if not accepted:
            return

        description, accepted = self.ask_project_text("Description:")
        if not accepted:
            return

        self.database.add_project(
            name=project_name,
            folder_path=folder_path.strip(),
            description=description.strip()
        )
        self.current_project = self.database.get_project_by_name(project_name)
        self.show_message(f"Project added: {project_name}")

    def open_chat_dialog(self):
        if self.chat_dialog is None:
            self.chat_dialog = ChatDialog(
                parent=self,
                current_project=self.current_project
            )
            self.chat_dialog.move(
                self.mapToGlobal(self.rect().center())
                - self.chat_dialog.rect().center()
            )
        else:
            self.chat_dialog.current_project = self.current_project

        self.chat_dialog.show()
        self.chat_dialog.raise_()
        self.chat_dialog.activateWindow()

    def show_context_menu(self, global_position):
        menu = QMenu(self)
        if self.roaming_enabled:
            roaming_action = menu.addAction("Pause roaming")
        else:
            roaming_action = menu.addAction("Resume roaming")
        menu.addSeparator()
        switch_project_action = menu.addAction("Switch Project")
        add_project_action = menu.addAction("Add Project")
        settings_action = menu.addAction("Settings")
        close_action = menu.addAction("Close RoboMate")

        selected_action = menu.exec(global_position)
        if selected_action == roaming_action:
            if self.roaming_enabled:
                self.pause_roaming()
            else:
                self.resume_roaming()
        elif selected_action == switch_project_action:
            self.show_project_selector()
        elif selected_action == add_project_action:
            self.add_new_project()
        elif selected_action == settings_action:
            self.show_message("Settings are coming soon.")
        elif selected_action == close_action:
            self.quit_robomate()

    def start_roaming_walk(self):
        if not self.roaming_enabled:
            return
        if self.is_sleepy or self.is_walking:
            return
        if self.is_waving:
            self.schedule_next_roam(100)
            return

        self.idle_wait_timer.stop()
        print("Starting roam")
        screen = QApplication.primaryScreen().availableGeometry()
        max_x = screen.right() - self.width() + 1
        max_y = screen.bottom() - self.height() + 1
        self.target_x = random.randint(screen.left(), max_x)
        self.target_y = random.randint(screen.top(), max_y)
        self.is_walking = True
        self.walk_frame = 0
        self.update()

    def update_walk(self):
        if not self.is_walking:
            return

        screen = QApplication.primaryScreen().availableGeometry()
        max_x = screen.right() - self.width() + 1
        max_y = screen.bottom() - self.height() + 1
        dx = self.target_x - self.x()
        dy = self.target_y - self.y()
        distance = hypot(dx, dy)

        if distance <= self.walk_speed:
            self.move(self.target_x, self.target_y)
            self.is_walking = False
            self.walk_frame = 0
            self.update()
            print("Reached target")
            if self.roaming_enabled:
                self.schedule_next_roam(random.randint(4000, 7000))
            return

        if abs(dx) >= abs(dy) and dx != 0:
            self.walk_direction = 1 if dx > 0 else -1

        step_x = dx / distance * self.walk_speed
        step_y = dy / distance * self.walk_speed
        new_x = round(self.x() + step_x)
        new_y = round(self.y() + step_y)
        new_x = max(screen.left(), min(new_x, max_x))
        new_y = max(screen.top(), min(new_y, max_y))

        self.walk_frame += 1

        self.update()

        self.move(new_x, new_y)

    def update_idle_animation(self):
        self.idle_animation_tick += 1
        float_step = 1
        float_limit = 5

        if self.is_sleepy:
            float_limit = 1
            if self.idle_animation_tick % 4 != 0:
                float_step = 0

        self.float_offset += self.float_direction * float_step

        if self.float_offset >= float_limit:
            self.float_direction = -1

        elif self.float_offset <= -float_limit:
            self.float_direction = 1

        # Ease toward each idle pose instead of snapping to it.
        self.gaze_offset += (self.target_gaze_offset - self.gaze_offset) * 0.2
        self.head_tilt += (self.target_head_tilt - self.head_tilt) * 0.2

        self.update()

    def update_idle_pose(self):
        poses = (
            "look_left",
            "normal",
            "look_right",
            "normal",
            "tilt",
            "normal",
            "normal",
        )
        self.idle_pose = poses[self.idle_pose_step]
        self.idle_pose_step = (self.idle_pose_step + 1) % len(poses)

        self.target_gaze_offset = 0.0
        self.target_head_tilt = 0.0
        if self.idle_pose == "look_left":
            self.target_gaze_offset = -11.0
        elif self.idle_pose == "look_right":
            self.target_gaze_offset = 11.0
        elif self.idle_pose == "tilt":
            self.target_head_tilt = -7.0

        self.update()

    def start_blink(self):
        if self.expression != "normal":
            return

        self.eyes_closed = True
        self.update()

        QTimer.singleShot(180, self.end_blink)

    def end_blink(self):
        self.eyes_closed = False
        self.update()

    def show_happy(self):
        if self.is_sleepy:
            return

        self.expression = "happy"

        self.show_message(
            "Yay! Let's build something! 🤖",
            duration=4000,
        )

        self.update()

        QTimer.singleShot(
            1500,
            self.reset_expression
        )

    def reset_expression(self):
        self.expression = "normal"
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        painter.save()

        if self.is_walking and self.walk_direction == -1:
            painter.translate(self.width(), 0)
            painter.scale(-1, 1)

        painter.translate(130, 137)
        painter.scale(0.65, 0.65)
        painter.translate(-160, -177)

        left_leg_x = 0
        right_leg_x = 0
        left_leg_y = 0
        right_leg_y = 0
        walk_bounce = 0

        if self.is_walking:
            walk_poses = (
                (6, -6, 3, -3, 0),
                (0, 0, 1, 1, 3),
                (-6, 6, -3, 3, 0),
                (0, 0, 1, 1, 3),
            )
            pose_index = (self.walk_frame // 4) % len(walk_poses)
            next_pose_index = (pose_index + 1) % len(walk_poses)
            pose_progress = (self.walk_frame % 4) / 4
            current_pose = walk_poses[pose_index]
            next_pose = walk_poses[next_pose_index]

            left_leg_x = current_pose[0] + (next_pose[0] - current_pose[0]) * pose_progress
            right_leg_x = current_pose[1] + (next_pose[1] - current_pose[1]) * pose_progress
            left_leg_y = current_pose[2] + (next_pose[2] - current_pose[2]) * pose_progress
            right_leg_y = current_pose[3] + (next_pose[3] - current_pose[3]) * pose_progress
            walk_bounce = current_pose[4] + (next_pose[4] - current_pose[4]) * pose_progress

        y = self.float_offset + walk_bounce
        left_leg_y += self.float_offset
        right_leg_y += self.float_offset
        arm_y = y

        outline = QPen(QColor("#b8d2dc"), 2)
        shell = QLinearGradient(0, 190 + y, 0, 290 + y)
        shell.setColorAt(0, QColor("#ffffff"))
        shell.setColorAt(1, QColor("#dcebf0"))

        # Short, chunky legs and oversized rounded feet.
        painter.setPen(outline)
        painter.setBrush(shell)
        painter.drawRoundedRect(131 + left_leg_x, 246 + left_leg_y, 21, 28, 10, 10)
        painter.drawRoundedRect(168 + right_leg_x, 246 + right_leg_y, 21, 28, 10, 10)
        painter.drawEllipse(112 + left_leg_x, 265 + left_leg_y, 51, 27)
        painter.drawEllipse(157 + right_leg_x, 265 + right_leg_y, 51, 27)

        # Chubby little arms with round shoulders and small hands.
        if self.is_waving:
            painter.drawRoundedRect(103, 207 + arm_y, 22, 23, 11, 11)
            painter.save()
            painter.translate(105, 216 + arm_y)
            painter.rotate(self.wave_angle)
            painter.drawRoundedRect(-25, -11, 27, 23, 11, 11)
            painter.drawEllipse(-25, -9, 23, 23)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#a6eaf5"))
            painter.drawEllipse(-18, -3, 8, 8)
            painter.restore()
            painter.setPen(outline)
            painter.setBrush(shell)
            painter.drawEllipse(99, 198 + arm_y, 32, 32)
        else:
            painter.drawRoundedRect(91, 207 + arm_y, 34, 23, 11, 11)
            painter.drawEllipse(99, 198 + arm_y, 32, 32)
            painter.drawEllipse(80, 209 + arm_y, 23, 23)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#a6eaf5"))
            painter.drawEllipse(87, 215 + arm_y, 8, 8)

        painter.setPen(outline)
        painter.setBrush(shell)
        painter.drawRoundedRect(195, 207 + arm_y, 34, 23, 11, 11)
        painter.drawEllipse(189, 198 + arm_y, 32, 32)
        painter.drawEllipse(217, 209 + arm_y, 23, 23)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#a6eaf5"))
        painter.drawEllipse(225, 215 + arm_y, 8, 8)

        # Scale the complete head, visor, and face together by 88%.
        painter.save()
        painter.translate(160, 134 + y)
        painter.rotate(self.head_tilt)
        painter.scale(0.88, 0.88)
        painter.translate(-160, -134 - y)

        # Small round ear panels peek out around the oversized head.
        painter.setPen(outline)
        painter.setBrush(shell)
        painter.drawEllipse(42, 111 + y, 48, 48)
        painter.drawEllipse(230, 111 + y, 48, 48)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#8beaff"))
        painter.drawEllipse(54, 123 + y, 24, 24)
        painter.drawEllipse(242, 123 + y, 24, 24)
        painter.setBrush(QColor("#d9f9ff"))
        painter.drawEllipse(60, 127 + y, 8, 8)
        painter.drawEllipse(248, 127 + y, 8, 8)

        # The large rounded head is the main feature of the silhouette.
        painter.setPen(outline)
        painter.setBrush(shell)
        painter.drawRoundedRect(58, 65 + y, 204, 137, 60, 60)
        painter.setPen(QPen(QColor("#ffffff"), 3))
        painter.drawArc(77, 72 + y, 166, 94, 35 * 16, 105 * 16)

        # Smooth dark visor, inset into the head.
        visor = QLinearGradient(0, 87 + y, 0, 180 + y)
        visor.setColorAt(0, QColor("#182a33"))
        visor.setColorAt(1, QColor("#05090c"))
        painter.setPen(Qt.NoPen)
        painter.setBrush(visor)
        painter.drawRoundedRect(77, 88 + y, 166, 87, 43, 43)

        # Bright cyan eyes keep their normal, blinking, and happy states.
        painter.setBrush(QColor("#3beaff"))
        gaze_offset = self.gaze_offset

        if self.expression == "sleepy":
            painter.setPen(QPen(QColor("#3beaff"), 3))
            painter.drawLine(118 + gaze_offset, 130 + y, 137 + gaze_offset, 130 + y)
            painter.drawLine(185 + gaze_offset, 130 + y, 204 + gaze_offset, 130 + y)
            painter.setPen(QPen(QColor("#8beaff"), 2))
            painter.drawText(218, 105 + y, "Zz")
        elif self.expression == "happy":
            painter.setPen(QPen(QColor("#3beaff"), 5))
            painter.drawArc(111 + gaze_offset, 121 + y, 31, 22, 0, 180 * 16)
            painter.drawArc(178 + gaze_offset, 121 + y, 31, 22, 0, 180 * 16)
        elif self.eyes_closed:
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(118 + gaze_offset, 132 + y, 19, 4, 2, 2)
            painter.drawRoundedRect(185 + gaze_offset, 132 + y, 19, 4, 2, 2)
        else:
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(118 + gaze_offset, 116 + y, 19, 27)
            painter.drawEllipse(185 + gaze_offset, 116 + y, 19, 27)

        # A small cyan arc makes a friendly smile.
        painter.setPen(QPen(QColor("#8beaff"), 3))
        painter.drawArc(151, 146 + y, 19, 11, 200 * 16, 140 * 16)
        painter.restore()

        # A round, chubby torso with a soft inset belly panel.
        belly = QLinearGradient(0, 204 + y, 0, 261 + y)
        belly.setColorAt(0, QColor("#f8feff"))
        belly.setColorAt(1, QColor("#e3f5f8"))
        painter.setPen(outline)
        painter.setBrush(shell)
        painter.drawRoundedRect(107, 197 + y, 106, 68, 34, 34)

        painter.setPen(Qt.NoPen)
        painter.setBrush(belly)
        painter.drawEllipse(117, 204 + y, 86, 55)

        # Small circular chest badge sits on the soft belly panel.
        painter.setPen(QPen(QColor("#a6eaf5"), 2))
        painter.setBrush(QColor("#d9f9ff"))
        painter.drawEllipse(148, 216 + y, 24, 24)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#3beaff"))
        painter.drawEllipse(155, 223 + y, 10, 10)
        painter.restore()

    def mousePressEvent(self, event):
        self.register_interaction()

        if event.button() == Qt.RightButton:
            self.show_context_menu(event.globalPosition().toPoint())
            event.accept()
            return

        if event.button() == Qt.LeftButton:
            self.left_press_active = True
            self.is_dragging = False
            self.press_position = event.globalPosition().toPoint()
            self.press_click_position = event.position().toPoint()
            self.drag_position = (
                self.press_position - self.frameGeometry().topLeft()
            )
            event.accept()

    def mouseMoveEvent(self, event):
        if self.left_press_active and event.buttons() & Qt.LeftButton:
            self.register_interaction()

            current_position = event.globalPosition().toPoint()
            movement = current_position - self.press_position
            if (
                not self.is_dragging
                and movement.manhattanLength() >= QApplication.startDragDistance()
            ):
                self.is_dragging = True

            if self.is_dragging:
                self.move(current_position - self.drag_position)

            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.left_press_active:
            was_dragging = self.is_dragging
            click_position = self.press_click_position
            self.left_press_active = False
            self.is_dragging = False

            if not was_dragging:
                if (
                    95 <= click_position.x() <= 165
                    and 146 <= click_position.y() <= 198
                ):
                    self.show_happy()
                self.open_chat_dialog()

            event.accept()


app = QApplication(sys.argv)

window = RoboMateWindow()
window.show()

sys.exit(app.exec())