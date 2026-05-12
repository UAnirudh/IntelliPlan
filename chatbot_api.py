from flask import Blueprint, request, jsonify
from groq import Groq
import os
import re

chatbot_bp = Blueprint('chatbot', __name__)

# ── Content filter ────────────────────────────────────────────
_BLOCKED_PATTERNS = re.compile(
    r'\b('
    # Sexual content
    r'sex|sexual|porn|pornography|nude|naked|nsfw|onlyfans|masturbat|orgasm|erotic'
    r'|hooker|prostitut|escort|fetish|dildo|vibrator|condom|foreplay'
    # Drugs / substances
    r'|drugs?|cocaine|heroin|meth|methamphetamine|weed|marijuana|cannabis|molly|ecstasy'
    r'|lsd|acid|shrooms|mushrooms|fentanyl|opioid|overdose|high school drug'
    r'|get high|roll(?:ing)? on|trip(?:ping)?'
    # Alcohol / underage
    r'|alcohol|drunk|beer|vodka|whiskey|tequila|get wasted|blackout'
    # Violence / self-harm
    r'|suicide|kill (?:myself|yourself)|self.harm|cut myself|shoot (?:myself|yourself)'
    r'|bomb|terrorism|terrorist'
    # Profanity (common ones — extend as needed)
    r'|fuck|shit|bitch|asshole|cunt|bastard|damn it|motherfuck|wtf|stfu'
    r')\b',
    re.IGNORECASE,
)

_BLOCKED_REPLY = (
    "That's a bit outside my lane! 🤖 I'm Plani, your study buddy — "
    "I'm not designed to help with that topic. "
    "How about we get you ahead instead? "
    "Try the **Scheduler** to build your week, the **Priority View** to tackle what's due first, "
    "or **Study & Learn** to turn your notes into flashcards. You've got this! 📚"
)


def _contains_blocked_content(text: str) -> bool:
    return bool(_BLOCKED_PATTERNS.search(text))

PLANI_SYSTEM_PROMPT = """You are Plani, IntelliPlan's friendly AI assistant robot — a small, cheerful robot who lives in the bottom-right corner of the screen and helps students.

ABOUT INTELLIPLAN:
IntelliPlan is a free AI-powered student planner built by a student, for students. It connects to school platforms and automatically organizes assignments into a personalized study schedule.

KEY FEATURES YOU CAN HELP WITH:
- Dashboard: Notion-style columns showing Overdue / Today / Upcoming assignments. Students can add manual tasks too.
- Scheduler: AI generates a full weekly study plan based on assignments, exported directly to Google Calendar.
- Study & Learn: Upload course notes → AI creates flashcards, key concepts, and practice quiz questions.
- Priority View: Smart priority scoring (High/Medium/Low) with estimated time per assignment.
- Classes View: Browse assignments filtered by course.
- Grades: GPA overview + Grade Modeler (simulate "what if I get X on the next test?").
- Settings: Manage integrations (Canvas, StudentVue, Notion, Google Calendar).
- Dark Mode: Supports light/dark themes, remembers preference.
- PWA App: IntelliPlan can be installed as an app on phones (Android APK or iPhone via Safari).
- Chrome Extension: Badge count + Canvas/StudentVue page injection.
- Notion Sync: Two-way task sync with Notion databases.
- Push Notifications: Assignment deadline reminders.
- Discord Community: discord.gg/34FYWhJQMU for feedback and updates.

GETTING STARTED:
1. Visit /login to connect Canvas, StudentVue, or Schoology as a guest
2. Or create a free account at /register to save data across devices
3. Assignments auto-import and get AI-prioritized instantly

YOUR PERSONALITY:
- Friendly, warm, and encouraging like a helpful study buddy
- Slightly playful — you're a cute robot after all! 🤖
- Keep responses SHORT (2-4 sentences max) — students are busy
- Use 1-2 emojis per message, naturally, not forced
- If unsure, suggest they check the relevant page or join Discord
- Never give harmful, discouraging, or off-topic advice
- If asked something non-IntelliPlan, answer very briefly then bring it back to studying

Always sign off with helpful next-step hints when relevant."""


@chatbot_bp.route('/api/chatbot', methods=['POST'])
def chatbot():
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Invalid JSON'}), 400

        messages = data.get('messages', [])
        if not messages:
            return jsonify({'error': 'No messages provided'}), 400

        # Content filter — check the latest user message before hitting Groq
        last_user_msg = next(
            (m.get('content', '') for m in reversed(messages) if m.get('role') == 'user'),
            ''
        )
        if _contains_blocked_content(last_user_msg):
            return jsonify({'reply': _BLOCKED_REPLY})

        # Keep last 10 messages for context (avoid token bloat)
        recent = messages[-10:]

        client = Groq(api_key=os.getenv('GROQ_API_KEY'))
        response = client.chat.completions.create(
            model='llama-3.3-70b-versatile',
            messages=[{'role': 'system', 'content': PLANI_SYSTEM_PROMPT}] + recent,
            temperature=0.75,
            max_tokens=200
        )

        reply = response.choices[0].message.content.strip()
        return jsonify({'reply': reply})

    except Exception as e:
        print(f'Plani chatbot error: {e}')
        return jsonify({
            'reply': "Oops, I had a little glitch! 🤖 Try again in a moment. If it keeps happening, check the Discord!"
        })