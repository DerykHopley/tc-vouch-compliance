# Sprint 3 project brief (from the course, pasted 2026-11-02)

Build an app with an AI agent on the backend. It should be useful and solve a real problem. Intended editor: VS Code.

## Task requirements

- **Agent purpose:** a clear purpose, why it's useful, the target users.
- **Core functionality:** the main features, done well, with the needed user interactions.
- **User interface:** user-friendly and intuitive, for all functions.
- **Technical implementation:** suitable tools and libraries, proper error handling, handles real-world use.
- **Documentation:** how to use it, examples of common use cases, technical decisions explained.

## Optional tasks

Bonus: for maximum points, at least **2 medium and 1 hard**.

Easy
- Ask an LLM to critique the solution (usability, security, prompt engineering).
- Give the agent a personality (formal, friendly, concise).
- Let the user choose from a list of LLMs.
- Expose model settings (temperature, max tokens) as sliders or fields.
- An interactive help feature or chatbot guide.

Medium
- Calculate and show token usage and cost.
- Long- or short-term memory in LangChain/LangGraph.
- One more function tool that calls an external API.
- User authentication and personalisation.
- A feedback loop: users rate responses, and the ratings improve the agent.
- 2 extra function tools (5 in total), with a UI to enable or disable them; a plugin system.
- Multi-model support (OpenAI, Anthropic, etc.).
- At least one security guard against misuse; keep developer settings (model, system prompt) apart from the user experience.

Hard
- Agentic RAG in the LangChain/LangGraph app.
- An LLM observability tool (LangSmith, Langfuse or others).
- An AI evaluation report with Ragas or DeepEval.
- An agent that learns from user feedback and adjusts its capabilities.
- Integration with external data sources (APIs or websites) to enrich its knowledge.

## Evaluation criteria

Problem definition
- A well-defined problem, and a clear account of how the app addresses it.

Understanding core concepts
- How agents work; differences between agent types.
- How function calling is implemented.
- Good code organisation.
- Error scenarios and edge cases.

Technical implementation
- Use of a front-end library.
- A relevant knowledge base for the domain, if applicable.
- Appropriate security.

Reflection and improvement
- The app's potential problems.
- Suggestions to improve the code and project.
- When to use prompt engineering, RAG, or agents.
