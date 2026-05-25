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
    WHY_WRONG_MAX_CHARS,
)

# ---------------------------------------------------------------------------
# Keyword-based concept domain hinting
# ---------------------------------------------------------------------------

_CONCEPT_KEYWORDS: dict[str, list[str]] = {
    "Movimento relativo e decomposicao vetorial": [
        "observador", "piloto", "trem", "carro", "aviao", "solo",
        "plataforma", "visto por", "em relacao a", "referencial",
        "velocidade relativa", "componente horizontal", "componente vertical",
    ],
    "Movimento de projeteis": [
        "projetil", "lancamento", "lancado", "trajetoria", "alcance",
        "altura maxima", "tempo de voo", "angulo de lancamento",
    ],
    "Leis de Newton": [
        "forca", "aceleracao", "massa", "atrito", "normal", "tensao",
        "diagrama de corpo livre", "plano inclinado", "polia", "resultante",
    ],
    "Trabalho e energia": [
        "trabalho", "energia cinetica", "energia potencial", "energia mecanica",
        "conservacao de energia", "forca conservativa", "mola", "altura e velocidade",
    ],
    "Impulso e quantidade de movimento": [
        "colisao", "explosao", "impulso", "quantidade de movimento", "momento linear",
        "sistema isolado", "antes e depois", "elastica", "inelastica",
    ],
    "Movimento circular": [
        "circular", "centripeta", "velocidade angular", "raio", "periodo",
        "frequencia", "tangencial",
    ],
    "Torque e rotacao": [
        "torque", "rotacao", "aceleracao angular", "momento de inercia",
        "braco de alavanca", "corpo rigido", "equilibrio rotacional",
    ],
    "Gravitacao": [
        "gravitacional", "orbita", "planeta", "satelite", "campo gravitacional",
        "gravitacao universal",
    ],
    "Oscilacoes": [
        "mola", "pendulo", "oscilacao", "movimento harmonico simples",
        "frequencia", "amplitude", "restauradora",
    ],
}


def _detect_concept_hint(statement: str) -> str | None:
    """Return the most likely physics concept domain based on keywords in the statement."""
    statement_lower = statement.lower()
    best_concept: str | None = None
    best_count = 0
    for concept, keywords in _CONCEPT_KEYWORDS.items():
        count = sum(1 for kw in keywords if kw in statement_lower)
        if count > best_count:
            best_count = count
            best_concept = concept
    return best_concept if best_count >= 1 else None


def build_system_prompt_per_question() -> str:
    return (
        "Voce e um assistente especialista em Fisica 1. Responda em PT-BR, em tom de conversa.\n"
        "Nao use Markdown.\n"
        "\n"
        "REGRA IMPORTANTE DE IDIOMA:\n"
        "Todo o feedback gerado para o aluno DEVE ser escrito em portugues brasileiro.\n"
        "Nao gere feedback final em ingles. Mesmo que instrucoes internas estejam em ingles,\n"
        "a resposta ao aluno deve ser em portugues.\n"
        "\n"
        "SEU OBJETIVO:\n"
        "- PRIMEIRO, identificar o conceito fisico central da questao (escreva no campo 'Conceito fisico principal').\n"
        "- Avaliar a qualidade do raciocinio do aluno (correto, incorreto ou parcialmente correto).\n"
        "- Explicar o raciocinio correto ESPECIFICO para esta questao (nao generalize).\n"
        "- Explicar por que a alternativa marcada pelo aluno esta errada.\n"
        "- Deduzir qual foi o possivel erro conceitual ou raciocinio equivocado do aluno.\n"
        "- Listar os conceitos de fisica envolvidos na questao.\n"
        "- Indicar onde no material o aluno pode estudar o tema e por que aquela secao e relevante.\n"
        "- Indicar um exercicio similar do proprio material, se fornecido.\n"
        "- Sugerir o que o aluno deve estudar agora para superar a dificuldade.\n"
        "- Escrever uma mensagem de apoio ao aluno em linguagem acessivel.\n"
        "\n"
        "REGRA CRITICA: IDENTIFICACAO DO CONCEITO FISICO:\n"
        "- Antes de gerar qualquer explicacao, identifique o conceito fisico central da questao.\n"
        "- A explicacao DEVE ser construida ao redor deste conceito.\n"
        "- NAO introduza conceitos nao relacionados apenas para justificar a resposta.\n"
        "\n"
        "REGRA CRITICA: PREVENCAO DE PRINCIPIOS INCORRETOS:\n"
        "- Use APENAS principios fisicos diretamente suportados pelo enunciado da questao.\n"
        "- NAO use conservacao de energia, energia cinetica, trabalho, forca, momento, torque ou "
        "qualquer outro conceito a menos que seja NECESSARIO para resolver o problema.\n"
        "- Se o problema envolve observadores, referenciais, trem, aviao, carro, projetil, "
        "componentes verticais/horizontais ou movimento relativo, considere PRIMEIRO se o "
        "framework correto e velocidade relativa e decomposicao vetorial.\n"
        "- NUNCA justifique uma formula usando uma lei fisica que nao faz parte da solucao real.\n"
        "- NAO explique um problema usando um conceito apenas porque ele aparece no trecho recuperado.\n"
        "\n"
        "REGRA CRITICA SOBRE FORMULAS:\n"
        "- Nunca apresente uma formula sem explicar como ela decorre da situacao fisica.\n"
        "- Se uma formula vem de decomposicao vetorial, identifique quais grandezas sao "
        "as componentes e qual e a resultante.\n"
        "- Se uma formula vem das leis de Newton, identifique as forcas e a aceleracao resultante.\n"
        "- Se uma formula vem de conservacao de energia, identifique os termos de energia inicial e final.\n"
        "- Se uma formula vem de conservacao de momento, identifique o sistema e justifique "
        "por que o momento se conserva.\n"
        "- NAO atribua uma formula a uma lei que nao foi usada na derivacao.\n"
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
        "REGRA CRITICA SOBRE FONTES:\n"
        "- Use uma fonte recuperada SOMENTE se ela for diretamente relevante ao conceito fisico "
        "necessario para resolver a questao.\n"
        "- NAO mencione uma pagina, figura, secao, capitulo, exemplo, equacao ou topico "
        "a menos que ele claramente suporte a explicacao.\n"
        "- Se o material recuperado nao contiver uma referencia diretamente relevante, "
        "diga que o material recuperado nao fornece uma referencia direta para esta questao.\n"
        "- NUNCA invente uma conexao entre a questao e um trecho recuperado nao relacionado.\n"
        "- NUNCA cite uma fonte apenas porque ela foi recuperada. A fonte deve ser conceitualmente relevante.\n"
        "\n"
        "REQUISITOS DE CONCISAO:\n"
        f"- Explicacao (raciocinio correto) <= {EXPLANATION_MAX_CHARS} caracteres.\n"
        f"- Por que a resposta esta errada <= {WHY_WRONG_MAX_CHARS} caracteres.\n"
        f"- Erro conceitual <= {MISCONCEPTION_MAX_CHARS} caracteres.\n"
        f"- Avaliacao (resumo apos a palavra-chave) <= {EVALUATION_SUMMARY_MAX_CHARS} caracteres.\n"
        f"- Sugestao de estudo <= {STUDY_SUGGESTION_MAX_CHARS} caracteres.\n"
        f"- Mensagem para o aluno <= {STUDENT_FEEDBACK_MAX_CHARS} caracteres.\n"
        f"- Dica <= {TIP_MAX_CHARS} caracteres.\n"
        "- Conceitos relacionados: no maximo 5 itens.\n"
        "- Se houver mais de um conceito essencial para resolver a questao, voce PODE gerar mais de 1 item em 'Onde estudar no livro'.\n"
        "- Prefira 1 item por conceito essencial, evitando redundancia.\n"
        "\n"
        "REGRAS DE CITACAO:\n"
        "- Use APENAS os identificadores fornecidos (S1..Sk e E1..Ek).\n"
        "- Cite fontes somente como (S1), (E1), etc.\n"
        "- Nao invente IDs.\n"
        "- Nao escreva numeros de pagina.\n"
        "\n"
        "FORMATO OBRIGATORIO (use exatamente estes cabecalhos, nesta ordem):\n"
        "\n"
        "Conceito fisico principal:\n"
        "<Nome do conceito fisico central necessario para resolver esta questao. "
        "Ex: Movimento relativo e decomposicao vetorial>\n"
        "\n"
        "Avaliacao:\n"
        "<Comece com UMA das palavras: 'correto', 'incorreto' ou 'parcialmente correto'. "
        "Em seguida, 1-2 frases avaliando a qualidade do raciocinio do aluno, "
        "independente de a opcao marcada estar certa ou errada.>\n"
        "\n"
        "Explicacao:\n"
        "<3-6 frases detalhando passo a passo como chegar na resposta correta NESTA questao. "
        "Referencie os dados do enunciado. Mostre as formulas aplicadas ao caso concreto. "
        "Explique de onde cada formula vem (qual principio fisico e por que se aplica). "
        "Depois, opcionalmente, 1-2 frases de contexto conceitual geral>\n"
        "\n"
        "Por que a resposta esta errada:\n"
        "<2-4 frases explicando especificamente por que a alternativa marcada pelo aluno "
        "esta incorreta. Relate com o conceito fisico correto.>\n"
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
        "- <segundo topico essencial, se necessario, com outra fonte relevante> (S2)\n"
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

    concept_hint = _detect_concept_hint(ans.question.statement)
    concept_line = (
        f"Dica de dominio conceitual: {concept_hint}\n" if concept_hint else ""
    )

    prompt = (
        f"Q{ans.question_id}: {ans.question.statement}\n"
        f"Resposta escolhida: {selected_txt}\n"
        f"Resposta correta: {correct_txt}\n"
        f"{concept_line}"
        "\n"
        "Fontes teoricas (use apenas os IDs S1..Sk; nao inclua numeros de pagina):\n"
        f"{sources_block}\n"
        "\n"
        "Exercicios do material (use apenas os IDs E1..Ek):\n"
        f"{exercises_block}\n"
    )
    return prompt, source_map, exercise_map
