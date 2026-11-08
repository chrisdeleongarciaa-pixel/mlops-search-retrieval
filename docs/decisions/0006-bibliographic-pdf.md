# 0006 · Perfil bibliográfico y PDF científico

**Adoptado para el incremento del 8 de noviembre de 2026.** El ámbito bibliográfico es inglés, ML/NLP, título y resumen, relevancia por artículo. LitSearch se fija en la revisión `9573fb284a1026c998df47024b888a163f0f0e25`. La selección de 300 consultas usa cuotas por procedencia y especificidad y una semilla registrada antes de evaluar rankings. Los positivos se heredan, y se conserva el estado de no juzgado para los demás artículos. El conjunto de desarrollo excluye componentes relacionados por artículos positivos o texto idéntico normalizado.

El modelo principal de reescritura es Qwen2.5-7B-Instruct. Se reutiliza la revisión del piloto. El control generativo es Gemma-3-1B-IT y requiere fijar revisión y acceso antes del ensayo. En la referencia clínica aportada, Qwen de 7B obtuvo mediana macro-F1 de 0,899 y Gemma de 1B 0,740 sobre 13 variables. La transferencia de ese resultado a reescritura bibliográfica es una hipótesis y la comparación incluye una diferencia de escala entre checkpoints. [Estudio citado](https://arxiv.org/html/2602.21374v1)

La primera transformación estructural usa GROBID 0.9.1 CRF y conserva TEI, texto canónico, encabezados, párrafos y localizadores. Se eligió el perfil CRF con concurrencia 1 y carga diferida para el host local. Docling queda como comparador cuando tablas, fórmulas o layouts revele una pérdida que deba medirse. Los controles de extracción y fragmentación se aplican antes de integrar una generación PDF con publicación.

Las decisiones de retención, cargas y límites son del laboratorio. Se reconsideran ante pérdida de evidencia, degradación de calidad, desbordamiento de recursos o incompatibilidad con el destino. El siguiente contrato de publicación debe conservar la semántica de revisión, generación, retiro y revalidación ya verificada.

[Contrato PDF](../contracts/scientific-pdf.md) · [Manifiesto LitSearch](../../config/litsearch-source.json) · [Carga](../../config/workload-profile.json) · [Reescritura](../../config/rewrite-experiment.json)
