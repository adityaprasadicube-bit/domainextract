# from .ollama_service import ask_ollama
#
#
# def explain_results(user_message, results):
#
#     prompt = f"""
# You are a telecom investigation assistant.
#
# User request:
# {user_message}
#
# Database results:
# {results}
#
# Explain the results clearly for investigators.
# Do not invent new data.
# """
#
#     explanation = ask_ollama(prompt)
#
#     return explanation