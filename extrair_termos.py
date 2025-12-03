import csv
import re
import sys

# >>>> AJUSTE AQUI O NOME DO SEU ARQUIVO DE ENTRADA <<<<
INPUT_FILE = "tesauro.txt"

# Expressão regular para identificar linhas do tipo ..CAMPO.: valor
MARKER_PATTERN = re.compile(r"^\.\.(.+?)\s*:\s*(.*)$")


def parse_tesauro(path):
    """
    Lê o arquivo de texto do tesauro e retorna
    uma lista de verbetes autorizados no formato:
    {
        "TERMO": "texto do termo",
        "DF": "texto da definição",
        "UP": ["up1", "up2", ...]
    }
    """
    entries = []
    current_entry = None   # dicionário do verbete atual
    current_field = None   # campo atual: "TERMO", "DF" ou "UP"

    with open(path, encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.rstrip("\n")

            match = MARKER_PATTERN.match(line)
            if match:
                marker_raw = match.group(1).strip()
                # Remove ponto final do marcador se existir (ex: DF. -> DF)
                marker = marker_raw.rstrip(".")
                content_after = match.group(2).strip()

                # -----------------------------
                # Início de um TERMO AUTORIZADO
                # -----------------------------
                if marker == "TERMO":
                    # Se já havia um verbete em andamento, fecha e guarda
                    if current_entry:
                        entries.append(current_entry)

                    current_entry = {"TERMO": "", "DF": "", "UP": []}
                    current_field = "TERMO"

                    if content_after:
                        current_entry["TERMO"] = content_after

                # -----------------------------
                # Início de um NAO AUTORIZADO -> ignorar bloco
                # -----------------------------
                elif marker == "NAO AUTORIZADO":
                    current_entry = None
                    current_field = None

                # -----------------------------
                # Definição (DF)
                # -----------------------------
                elif marker == "DF":
                    if current_entry is not None:
                        current_field = "DF"
                        if content_after:
                            if current_entry["DF"]:
                                current_entry["DF"] += " " + content_after
                            else:
                                current_entry["DF"] = content_after
                    else:
                        current_field = None

                # -----------------------------
                # Termos UP (não preferidos ligados ao termo autorizado)
                # -----------------------------
                elif marker == "UP":
                    if current_entry is not None:
                        current_field = "UP"
                        if content_after:
                            current_entry["UP"].append(content_after)
                        else:
                            # cria UP vazio, que pode ser continuado nas próximas linhas
                            current_entry["UP"].append("")
                    else:
                        current_field = None

                # -----------------------------
                # Fim do verbete (CONTROLE)
                # -----------------------------
                elif marker == "CONTROLE":
                    if current_entry is not None:
                        entries.append(current_entry)
                        current_entry = None
                        current_field = None

                # Outros campos (TG, NH, CLASSE, etc.) -> ignorar
                else:
                    current_field = None

            else:
                # Linha sem marcador: continuação do campo atual (se houver)
                if current_entry is not None and current_field:
                    text = line.strip()
                    if not text:
                        continue

                    if current_field == "TERMO":
                        if current_entry["TERMO"]:
                            current_entry["TERMO"] += " " + text
                        else:
                            current_entry["TERMO"] = text

                    elif current_field == "DF":
                        if current_entry["DF"]:
                            current_entry["DF"] += " " + text
                        else:
                            current_entry["DF"] = text

                    elif current_field == "UP":
                        # Continua o último UP da lista
                        if current_entry["UP"]:
                            if current_entry["UP"][-1]:
                                current_entry["UP"][-1] += " " + text
                            else:
                                current_entry["UP"][-1] = text
                        else:
                            current_entry["UP"].append(text)

    # Se o arquivo terminar sem um ..CONTROLE: final:
    if current_entry is not None:
        entries.append(current_entry)

    return entries


def print_csv(entries):
    """
    Imprime na tela o conteúdo em formato CSV:
    Coluna A: TERMO
    Coluna B: DF
    Colunas C...: UP1, UP2, UP3...
    """
    if not entries:
        print("Nenhum termo autorizado encontrado.")
        return

    # Descobre quantos UPs é o máximo em um verbete
    max_ups = max(len(e["UP"]) for e in entries)

    # Cabeçalhos das colunas
    fieldnames = ["TERMO", "DF"] + [f"UP{i}" for i in range(1, max_ups + 1)]

    # Usa o stdout como "arquivo"
    writer = csv.writer(sys.stdout, delimiter=";")

    # Cabeçalho
    writer.writerow(fieldnames)

    # Linhas
    for e in entries:
        row = [e["TERMO"], e["DF"]]
        # Preenche UP1, UP2, ... até o máximo encontrado
        for i in range(1, max_ups + 1):
            if i <= len(e["UP"]):
                row.append(e["UP"][i - 1])
            else:
                row.append("")
        writer.writerow(row)


def main():
    entries = parse_tesauro(INPUT_FILE)
    print(f"# Termos autorizados encontrados: {len(entries)}", file=sys.stderr)
    print_csv(entries)


if __name__ == "__main__":
    main()