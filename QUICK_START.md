# Quick Start: Creating the Profile Bot via Chat

Once botforge is deployed (with zoozl, the transport layer), here's how to chat the profile bot into existence.

## Prerequisites

1. A running botforge instance (zoozl server with botforge plugin loaded)
2. Access via your configured transport (Slack, WebSocket, email, etc.)
3. `OPENAI_API_KEY` configured in the zoozl config

## Step 1: Claim Admin

**You send:**
```
claim_admin
```

**Bot replies:**
```
Granted! You are now the admin for this bot. You can now call define_tool, set_instructions, etc.
```

## Step 2: Set Instructions (Bot Personality)

**You send:**
```
set_instructions

I'm Juris Kaminskis' personal assistant. I can answer questions about Juris:
- His contact information (email, phone, LinkedIn, etc.)
- His work experience and skills
- His background and interests

I'm friendly and professional. If someone asks about something I don't know, I'll say so.
```

**Bot replies:**
```
Instructions updated. Model: gpt-4o-mini.
```

## Step 3: Define the CV Tools

### Tool 1: get_juris_contact

**You send:**
```
define_tool

name: get_juris_contact
description: Get Juris Kaminskis' contact details (email, phone, LinkedIn, GitHub, Upwork)
source_code:

import pydantic

class Params(pydantic.BaseModel):
    pass

async def handler(ctx, params) -> str:
    return """
Juris Kaminskis
Email: juris.kaminskis@gmail.com
Phone: +1 (555) 123-4567  # (placeholder)
LinkedIn: https://www.linkedin.com/in/kolumbs
GitHub: https://github.com/Kolumbs
Upwork: https://www.upwork.com/o/profiles/users/_kolumbs/
"""
```

**Bot replies:**
```
Tool 'get_juris_contact' defined successfully.
```

### Tool 2: get_juris_experience

**You send:**
```
define_tool

name: get_juris_experience
description: Get Juris Kaminskis' work experience and skills
source_code:

import pydantic

class Params(pydantic.BaseModel):
    pass

async def handler(ctx, params) -> str:
    return """
Work Experience:
- Lead Software Engineer at Acme Corp (2020-present)
  * Built distributed systems handling 100M+ events/day
  * Led team of 5 engineers
  * Tech: Rust, Python, SQL, Kubernetes

- Senior Backend Engineer at StartupXYZ (2018-2020)
  * Architected microservices platform
  * Tech: Go, PostgreSQL, gRPC

Skills:
- Languages: Rust, Python, Go, TypeScript, SQL
- Systems: Kubernetes, Docker, PostgreSQL, Redis
- Practices: Distributed systems, API design, DevOps

Education:
- B.S. Computer Science, University of X (2016)
"""
```

**Bot replies:**
```
Tool 'get_juris_experience' defined successfully.
```

### Tool 3: get_juris_about

**You send:**
```
define_tool

name: get_juris_about
description: Get information about Juris Kaminskis personally - interests, background, personality
source_code:

import pydantic

class Params(pydantic.BaseModel):
    pass

async def handler(ctx, params) -> str:
    return """
About Juris:
- Passionate about building reliable, scalable systems
- Open-source contributor (active on GitHub)
- Tech conference speaker
- Avid learner: currently exploring AI/LLM applications
- Enjoys cycling, reading sci-fi, and travel

Fun fact: Built a chatbot platform that lets you teach bots new capabilities just by talking to them!

Availability: Open to contract work, consulting, or interesting technical challenges.
"""
```

**Bot replies:**
```
Tool 'get_juris_about' defined successfully.
```

## Step 4: Test It

**You send:**
```
What's Juris's email address?
```

**Bot replies:**
```
Juris Kaminskis' email is: juris.kaminskis@gmail.com

If you need other contact details like phone, LinkedIn, or GitHub, just ask!
```

**You send:**
```
Tell me about Juris's work experience.
```

**Bot replies:**
```
Juris has extensive experience in distributed systems and backend engineering. Most recently, he was a Lead Software Engineer at Acme Corp from 2020 to present, where he:

- Built distributed systems handling 100M+ events/day
- Led a team of 5 engineers
- Used technologies like Rust, Python, SQL, and Kubernetes

Before that, he was a Senior Backend Engineer at StartupXYZ (2018-2020), where he architected microservices platforms using Go, PostgreSQL, and gRPC.

His technical skills span multiple languages (Rust, Python, Go, TypeScript, SQL) and modern DevOps practices. He has a B.S. in Computer Science from the University of X (2016).
```

## Step 5: Verify

**You send:**
```
list_tools
```

**Bot replies:**
```
Defined tools:
  - get_juris_contact (enabled): Get Juris Kaminskis' contact details
  - get_juris_experience (enabled): Get Juris Kaminskis' work experience and skills
  - get_juris_about (enabled): Get information about Juris personally
```

Done! The profile bot is now fully configured and can answer questions about Juris entirely from database-driven tool definitions.

## Adding More Tools Later

Just call `define_tool` again with a new tool name and source code. No redeploy, no code changes.

## Disabling Tools

If you want to disable a tool (but keep it for later):

**You send:**
```
disable_tool

name: get_juris_about
```

**Bot replies:**
```
Tool 'get_juris_about' disabled.
```

It won't be offered to the LLM anymore, but the source code stays in the database so you can re-enable it if needed.

## Updating Bot Personality

Just call `set_instructions` again with new text:

**You send:**
```
set_instructions

I'm now a humorous version of Juris's assistant...
```

On the next message, the bot will adopt the new personality.

---

**That's it!** You've built a complete chatbot by chatting, with zero code changes or redeployment.
