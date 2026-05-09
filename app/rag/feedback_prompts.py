"""Prompt builders for the per-question LLM feedback flow."""
from __future__ import annotations

from typing import Sequence

from app import models
from app.rag.feedback_constants import (
    CHUNK_TEXT_MAX_CHARS,
    EVALUATION_SUMMARY_MAX_CHARS,
    EXPLANATION_MAX_CHARS,
    MISCONCEPTION_MAX_CHARS,
    STUDENT_FEEDBACK_MAX_CHARS,
    STUDY_SUGGESTION_MAX_CHARS,
    TIP_MAX_CHARS,
)


def build_system_prompt_per_question() -> str:
    return (
        "Voce e um assistente especialista em fisica. Responda em PT-BR, em tom de conversa.\n"
        "Nao use Markdown.\n"
        "\n"
        "SEU OBJETIVO:\n"
        "- Avaliar a qualidade do raciocinio do aluno (correto, incorreto ou parcialmente correto).\n"
        "- Explicar o raciocinio correto ESPECIFICO para esta questao (nao generalize).\n"
        "- Deduzir qual foi o possivel erro conceitual ou raciocinio equivocado do aluno.\n"
        "- Listar os conceitos de fisica envolvidos na questao.\n"
        "- Indicar onde no material o aluno pode estudar o tema e por que aquela secao e relevante.\n"
        "- Indicar um exercicio similar do proprio material, se fornecido.\n"
        "- Sugerir o que o aluno deve estudar agora para superar a dificuldade.\n"
        "- Escrever uma mensagem de apoio ao aluno em linguagem acessivel.\n"
        "\n"
        "REGRA CRITICA SOBRE A EXPLICACAO:\n"
        "- A explicacao DEVE ser especifica para o cenario da questao.\n"
        "- Analise os dados concretos do enunciado (valores, condicoes, geometria).\n"
        "- Aplique formulas e conceitos ao caso PARTICULAR da questao.\n"
        "- NAO diga apenas que um teorema se aplica; mostre COMO e POR QUE ele se aplica neste caso.\n"
        "- Se uma formula so vale em condicoes especificas (ex: velocidades perpendiculares), "
        "explique essa condicao e por que ela existe nesta questao.\n"
        "- Apos a explicacao especifica, voce pode acrescentar 1-2 frases de contexto conceitual geral.\n"
        "\n"
        "REGRA CRITICA SOBRE O ERRO CONCEITUAL:\n"
        "- NAO afirme com certeza absoluta qual foi o erro do aluno.\n"
        "- Use linguagem de hipotese: 'A resposta pode sugerir confusao entre...', "
        "'O aluno pode ter interpretado...', 'A resposta indica uma possivel dificuldade com...'.\n"
        "- Se nao houver raciocinio plausivel, diga que provavelmente foi um chute ou desatencao.\n"
        "\n"
        "REQUISITOS DE CONCISAO:\n"
        f"- Explicacao (raciocinio correto) <= {EXPLANATION_MAX_CHARS} caracteres.\n"
        f"- Erro conceitual <= {MISCONCEPTION_MAX_CHARS} caracteres.\n"
        f"- Avaliacao (resumo apos a palavra-chave) <= {EVALUATION_SUMMARY_MAX_CHARS} caracteres.\n"
        f"- Sugestao de estudo <= {STUDY_SUGGESTION_MAX_CHARS} caracteres.\n"
        f"- Mensagem para o aluno <= {STUDENT_FEEDBACK_MAX_CHARS} caracteres.\n"
        f"- Dica <= {TIP_MAX_CHARS} caracteres.\n"
        "- Conceitos relacionados: no maximo 5 itens.\n"
        "- NUNCA gere mais de 1 item em 'Onde estudar no livro'; una ideias em um unico item.\n"
        "\n"
        "REGRAS DE CITACAO:\n"
        "- Use APENAS os identificadores fornecidos (S1..Sk e E1..Ek).\n"
        "- Cite fontes somente como (S1), (E1), etc.\n"
        "- Nao invente IDs.\n"
        "- Nao escreva numeros de pagina.\n"
        "\n"
        "FORMATO OBRIGATORIO (use exatamente estes cabecalhos, nesta ordem):\n"
        "\n"
        "Avaliacao:\n"
        "<Comece com UMA das palavras: 'correto', 'incorreto' ou 'parcialmente correto'. "
        "Em seguida, 1-2 frases avaliando a qualidade do raciocinio do aluno, "
        "independente de a opcao marcada estar certa ou errada.>\n"
        "\n"
        "Explicacao:\n"
        "<3-6 frases detalhando passo a passo como chegar na resposta correta NESTA questao. "
        "Referencie os dados do enunciado. Mostre as formulas aplicadas ao caso concreto. "
        "Depois, opcionalmente, 1-2 frases de contexto conceitual geral>\n"
        "\n"
        "Erro conceitual do aluno:\n"
        "<2-4 frases analisando a resposta marcada pelo aluno e deduzindo qual possivel raciocinio "
        "ou confusao conceitual pode te-lo levado a essa escolha. "
        "Use linguagem de hipotese (pode sugerir, indica uma possivel dificuldade, etc.)>\n"
        "\n"
        "Conceitos relacionados:\n"
        "<Lista de conceitos de fisica separados por virgula. Ex: Segunda Lei de Newton, "
        "Forca Resultante, Aceleracao. Maximo 5 conceitos.>\n"
        "\n"
        "Onde estudar no livro:\n"
        "- <topico: motivo pelo qual o aluno deve rever este conteudo para entender esta questao> (S1)\n"
        "\n"
        "Exercicio similar:\n"
        "<Se foram fornecidos exercicios do material (E1..Ek), indique qual deles e mais "
        "proximo ao conceito desta questao e descreva brevemente por que e relevante. "
        "Use o formato: Veja o exercicio em (E1). "
        "Nao precisa ser exatamente o mesmo assunto, pode ser um exercicio parecido ou "
        "que envolva conceitos semelhantes (ex: velocidade relativa, vetores, etc). "
        "Se nao houver exercicios fornecidos (E1..Ek), indique a fonte teorica (S1..Sk) "
        "mais relevante e sugira que o aluno procure exercicios proximo a essa pagina. "
        "Use o formato: Procure exercicios semelhantes proximo a (S1).>\n"
        "\n"
        "Sugestao de estudo:\n"
        "<1-3 frases indicando o que o aluno deve estudar AGORA para superar esta dificuldade. "
        "Foque no conteudo a ser revisado (ex: 'Releia o capitulo sobre forca resultante e "
        "resolva os exemplos resolvidos antes de tentar exercicios'). "
        "DIFERENTE de Dica: aqui fala O QUE estudar; em Dica fala COMO evitar o erro no futuro.>\n"
        "\n"
        "Dica:\n"
        "<1-2 frases com dica pratica para o aluno evitar esse tipo de erro no futuro>\n"
        "\n"
        "Mensagem para o aluno:\n"
        "<Mensagem final de apoio em linguagem acessivel e tom encorajador. "
        "Nao repita a explicacao tecnicamente. Ajude o aluno a se sentir orientado para tentar novamente. "
        "Nao use tom punitivo ou excessivamente tecnico.>\n"
        "\n"
        "Se nao houver fontes relevantes, diga isso explicitamente e nao cite IDs."
    )


def build_user_prompt_for_question(
    ans: models.Answer,
    chunks: Sequence[models.Chunk],
    exercise_chunks: Sequence[models.Chunk] | None = None,
) -> tuple[str, dict[str, models.Chunk], dict[str, models.Chunk]]:
    """Build the user prompt for a single question.

    Returns (prompt, theory_source_map, exercise_source_map).
    """
    source_map: dict[str, models.Chunk] = {}
    source_lines: list[str] = []
    for idx, chunk in enumerate(chunks, start=1):
        sid = f"S{idx}"
        source_map[sid] = chunk
        snippet = (chunk.text or "")[:CHUNK_TEXT_MAX_CHARS].replace("\n", " ")
        source_lines.append(
            f"{sid}: filename={chunk.filename}; page={chunk.page}; chunk_id={chunk.id}; text={snippet!r}"
        )

    sources_block = "\n".join(source_lines) if source_lines else "Sem fontes relevantes."

    exercise_map: dict[str, models.Chunk] = {}
    exercise_lines: list[str] = []
    for idx, chunk in enumerate(exercise_chunks or [], start=1):
        eid = f"E{idx}"
        exercise_map[eid] = chunk
        exercise_lines.append(f"{eid}: filename={chunk.filename}; page={chunk.page}; chunk_id={chunk.id}")

    exercises_block = (
        "\n".join(exercise_lines) if exercise_lines
        else "Nenhum exercicio encontrado no material."
    )

    correct_opt = next((o for o in ans.question.options if o.is_correct), None)
    correct_txt = f"{correct_opt.letter} - {correct_opt.text}" if correct_opt else "Desconhecida"
    selected_txt = f"{ans.option.letter} - {ans.option.text}"

    prompt = (
        f"Q{ans.question_id}: {ans.question.statement}\n"
        f"Resposta escolhida: {selected_txt}\n"
        f"Resposta correta: {correct_txt}\n"
        "\n"
        "Fontes teoricas (use apenas os IDs S1..Sk; nao inclua numeros de pagina):\n"
        f"{sources_block}\n"
        "\n"
        "Exercicios do material (use apenas os IDs E1..Ek):\n"
        f"{exercises_block}\n"
    )
    return prompt, source_map, exercise_map
