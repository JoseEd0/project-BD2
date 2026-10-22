"""La raíz de los errores que son culpa de lo que se pidió.

Una consulta mal escrita, un valor que no cabe en su columna, un archivo que no se puede
cargar o un bloqueo que no llega son errores **del usuario**: el API los responde como tales
y el frontend los muestra. Cualquier otra excepción es un fallo del motor, y disfrazarla de
error del usuario escondería el bug. Cada módulo deriva de aquí la base de sus errores, de
modo que un módulo nuevo no tiene que anotarse en ninguna lista.
"""


class DomainError(Exception):
    """El motor no puede hacer lo que se le pidió, por cómo se le pidió."""
