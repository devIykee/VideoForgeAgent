"""Programmatic South Park-style talking-head avatar generator.

Everything is drawn with Pillow — no external APIs, no asset packs. For each
character we render five state frames (idle, three talking mouth shapes, and a
blink) and stitch them into short per-segment MP4 clips with FFmpeg. When a
character is speaking we cycle the talking frames; when idle we hold the idle
frame with an occasional blink.
"""

import os
import subprocess

from config import CharacterConfig


class AvatarGenerator:
    """Draws and animates cartoon avatars for both characters."""

    AVATAR_SIZE = (300, 380)  # width x height of an avatar panel

    def _hex_to_rgb(self, hex_color: str) -> tuple:
        """Convert ``#RRGGBB`` (or ``RRGGBB``) to an (R, G, B) tuple."""
        hex_color = hex_color.lstrip("#")
        if len(hex_color) != 6:
            hex_color = "3B6BB5"  # safe default (Steve blue)
        return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))

    def _get_skin_colors(self, skin: str, shirt_color: str) -> dict:
        """Return the color palette for a given skin + shirt color."""
        skin_palettes = {
            "steve":    {"skin": "#C68642", "hair": "#5C4033", "eyes": "#4A90D9"},
            "alex":     {"skin": "#C68642", "hair": "#CB6D2A", "eyes": "#4A90D9"},
            "creeper":  {"skin": "#4CAF50", "hair": "#388E3C", "eyes": "#000000"},
            "enderman": {"skin": "#1A1A1A", "hair": "#1A1A1A", "eyes": "#9B59B6"},
            "custom":   {"skin": "#C68642", "hair": "#5C4033", "eyes": "#4A90D9"},
        }
        palette = skin_palettes.get(skin, skin_palettes["steve"]).copy()
        palette["shirt"] = shirt_color
        return palette

    def _draw_face(self, draw, colors: dict, mouth_state: str,
                   eyes_state: str, w: int, h: int) -> None:
        """Draw a cartoon face/body onto an ImageDraw surface."""
        head_x, head_y = w // 2, int(h * 0.30)
        head_r = int(w * 0.35)
        skin_rgb = self._hex_to_rgb(colors["skin"])
        hair_rgb = self._hex_to_rgb(colors["hair"])
        eye_rgb = self._hex_to_rgb(colors["eyes"])
        shirt_rgb = self._hex_to_rgb(colors["shirt"])

        # Hair (slightly larger circle behind the head).
        draw.ellipse([
            head_x - head_r - 5, head_y - head_r - 8,
            head_x + head_r + 5, head_y + head_r,
        ], fill=hair_rgb)

        # Head.
        draw.ellipse([
            head_x - head_r, head_y - head_r,
            head_x + head_r, head_y + head_r,
        ], fill=skin_rgb, outline=(0, 0, 0), width=2)

        # Eyes.
        eye_y = head_y - int(head_r * 0.1)
        eye_offset = int(head_r * 0.35)

        if eyes_state == "open":
            for sign in (-1, 1):
                cx = head_x + sign * eye_offset
                draw.ellipse([cx - 12, eye_y - 12, cx + 12, eye_y + 12],
                             fill=(255, 255, 255), outline=(0, 0, 0), width=1)
                draw.ellipse([cx - 6, eye_y - 6, cx + 6, eye_y + 6], fill=eye_rgb)
                draw.ellipse([cx - 3, eye_y - 3, cx + 3, eye_y + 3], fill=(0, 0, 0))
        elif eyes_state == "blink":
            for sign in (-1, 1):
                cx = head_x + sign * eye_offset
                draw.line([cx - 10, eye_y, cx + 10, eye_y], fill=(0, 0, 0), width=3)

        # Mouth.
        mouth_y = head_y + int(head_r * 0.45)
        mouth_w = int(head_r * 0.45)

        if mouth_state == "closed":
            draw.line([head_x - mouth_w // 2, mouth_y,
                       head_x + mouth_w // 2, mouth_y],
                      fill=(80, 40, 40), width=3)
        elif mouth_state == "slightly_open":
            draw.ellipse([
                head_x - mouth_w // 2, mouth_y - 5,
                head_x + mouth_w // 2, mouth_y + 8,
            ], fill=(80, 40, 40), outline=(0, 0, 0), width=1)
        elif mouth_state == "open":
            draw.ellipse([
                head_x - mouth_w // 2, mouth_y - 8,
                head_x + mouth_w // 2, mouth_y + 18,
            ], fill=(80, 40, 40), outline=(0, 0, 0), width=1)
            # Teeth.
            draw.rectangle([
                head_x - mouth_w // 2 + 4, mouth_y - 4,
                head_x + mouth_w // 2 - 4, mouth_y + 2,
            ], fill=(240, 240, 240))

        # Body (trapezoid below the head).
        body_top_y = head_y + head_r - 5
        body_bot_y = int(h * 0.95)
        body_top_w = int(head_r * 1.0)
        body_bot_w = int(head_r * 1.3)
        body_points = [
            (head_x - body_top_w, body_top_y),
            (head_x + body_top_w, body_top_y),
            (head_x + body_bot_w, body_bot_y),
            (head_x - body_bot_w, body_bot_y),
        ]
        draw.polygon(body_points, fill=shirt_rgb, outline=(0, 0, 0))

        # Neck.
        neck_w = int(head_r * 0.3)
        draw.rectangle([
            head_x - neck_w, head_y + head_r - 10,
            head_x + neck_w, body_top_y + 5,
        ], fill=skin_rgb)

    def generate_frame(self, colors: dict, mouth_state: str,
                       eyes_state: str, label: str):
        """Render a single avatar frame and return it as a PIL Image."""
        from PIL import Image, ImageDraw

        w, h = self.AVATAR_SIZE
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Semi-transparent rounded background panel.
        draw.rounded_rectangle(
            [0, 0, w - 1, h - 1], radius=20,
            fill=(20, 20, 20, 180), outline=(255, 255, 255, 80), width=2,
        )

        self._draw_face(draw, colors, mouth_state, eyes_state, w, h)

        # Name label at the bottom.
        label_y = h - 35
        draw.rectangle([10, label_y, w - 10, h - 10], fill=(0, 0, 0, 160))
        draw.text((w // 2, label_y + 12), label, fill=(255, 255, 255), anchor="mm")

        return img

    def create_avatar_states(self, char: CharacterConfig, job_id: str) -> dict:
        """Render all mouth/eye state frames for a character. Returns {state: png}."""
        colors = self._get_skin_colors(char.avatar_skin, char.shirt_color)
        avatar_dir = f"/tmp/minecraftcast/{job_id}/avatars/{char.name}"
        os.makedirs(avatar_dir, exist_ok=True)

        states: dict = {}
        combinations = [
            ("idle",      "closed",        "open"),
            ("talking_1", "slightly_open", "open"),
            ("talking_2", "open",          "open"),
            ("talking_3", "slightly_open", "open"),
            ("blink",     "closed",        "blink"),
        ]
        for state_name, mouth, eyes in combinations:
            img = self.generate_frame(colors, mouth, eyes, char.name)
            path = os.path.join(avatar_dir, f"{state_name}.png")
            img.save(path, "PNG")
            states[state_name] = path

        return states

    def create_segment_video(self, char: CharacterConfig, is_talking: bool,
                             duration: float, job_id: str, seg_idx: int,
                             char_slot: str) -> str:
        """Build the avatar clip for one segment. Returns the output MP4 path.

        ``is_talking`` animates the mouth; otherwise the avatar holds idle and
        blinks every ~3 seconds.
        """
        avatar_dir = f"/tmp/minecraftcast/{job_id}/avatars/{char.name}"
        output_path = f"/tmp/minecraftcast/{job_id}/avatars/seg_{seg_idx:03d}_{char_slot}.mp4"

        fps = 8
        total_frames = max(1, int(duration * fps))

        frames: list[str] = []
        if is_talking:
            cycle = ["talking_1", "talking_2", "talking_3", "talking_2"]
            for i in range(total_frames):
                frames.append(os.path.join(avatar_dir, f"{cycle[i % len(cycle)]}.png"))
        else:
            for i in range(total_frames):
                if i % 24 in (0, 1):  # blink for 2 frames every 3 seconds
                    frames.append(os.path.join(avatar_dir, "blink.png"))
                else:
                    frames.append(os.path.join(avatar_dir, "idle.png"))

        # Write the FFmpeg concat frame list.
        list_path = (
            f"/tmp/minecraftcast/{job_id}/avatars/seg_{seg_idx:03d}_{char_slot}_frames.txt"
        )
        with open(list_path, "w") as f:
            for frame_path in frames:
                f.write(f"file '{frame_path}'\n")
                f.write(f"duration {1 / fps:.4f}\n")
            # Repeat the last frame once so concat honors the final duration.
            if frames:
                f.write(f"file '{frames[-1]}'\n")

        subprocess.run([
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", list_path,
            "-vf", "fps=8,scale=300:380:flags=lanczos",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-preset", "ultrafast",
            output_path,
        ], check=True, capture_output=True)

        return output_path
