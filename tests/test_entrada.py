import json
from datetime import date

import pytest

from triagem import prompt
from triagem.entrada import ErroEntrada, de_arquivo, de_texto


def _gravar(tmp_path, dados):
    caminho = tmp_path / "entrada.json"
    caminho.write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")
    return caminho


def test_objeto_vira_lista_de_um(tmp_path):
    chamados, era_lista = de_arquivo(_gravar(tmp_path, {"id": "A", "texto": "oi"}))
    assert not era_lista
    assert [c.id for c in chamados] == ["A"]


def test_lista_preserva_ordem(tmp_path, exemplos):
    chamados, era_lista = de_arquivo(_gravar(tmp_path, exemplos))
    assert era_lista
    assert [c.id for c in chamados] == [e["id"] for e in exemplos]


def test_texto_ausente_gera_erro(tmp_path):
    with pytest.raises(ErroEntrada, match="texto"):
        de_arquivo(_gravar(tmp_path, [{"id": "A", "assunto": "sem texto"}]))


def test_texto_vazio_gera_erro():
    with pytest.raises(ErroEntrada):
        de_texto("   ")


def test_canal_invalido_gera_erro(tmp_path):
    with pytest.raises(ErroEntrada, match="canal"):
        de_arquivo(_gravar(tmp_path, {"texto": "oi", "canal": "whatsapp"}))


def test_id_gerado_quando_falta(tmp_path):
    chamados, _ = de_arquivo(_gravar(tmp_path, [{"texto": "a"}, {"id": "X", "texto": "b"}, {"texto": "c"}]))
    assert [c.id for c in chamados] == ["CH-001", "X", "CH-003"]
    assert de_texto("oi").id == "CH-001"


def test_gabarito_fica_fora_do_prompt(tmp_path, exemplos):
    chamados, _ = de_arquivo(_gravar(tmp_path, exemplos[:1]))
    assert chamados[0].gabarito is not None  # disponível para a avaliação
    _, user = prompt.montar(chamados[0])
    assert "gabarito" not in user
    assert "gabarito" not in chamados[0].model_dump()


def test_data_referencia_vai_para_o_prompt(tmp_path):
    chamados, _ = de_arquivo(_gravar(tmp_path, {"texto": "boleto vence dia 15", "data_referencia": "2026-10-14"}))
    assert chamados[0].data_referencia == date(2026, 10, 14)
    _, user = prompt.montar(chamados[0])
    assert user.startswith("Data de referência (hoje): 14/10/2026, quarta-feira.")
    assert user.index("Data de referência") < user.index("<chamado>")  # fora do bloco do cliente


def test_sem_data_referencia_fica_nao_informada():
    _, user = prompt.montar(de_texto("oi"))
    assert user.startswith("Data de referência: não informada.")


def test_data_referencia_invalida_gera_erro(tmp_path):
    with pytest.raises(ErroEntrada, match="data_referencia"):
        de_arquivo(_gravar(tmp_path, {"texto": "oi", "data_referencia": "amanhã"}))


def test_json_invalido_e_arquivo_inexistente(tmp_path):
    ruim = tmp_path / "ruim.json"
    ruim.write_text("{nao é json", encoding="utf-8")
    with pytest.raises(ErroEntrada, match="JSON inválido"):
        de_arquivo(ruim)
    with pytest.raises(ErroEntrada, match="não encontrado"):
        de_arquivo(tmp_path / "nada.json")
