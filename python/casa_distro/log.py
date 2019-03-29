# coding: utf-8 

def getLogFile(verbose, openmode='w+'):
    import ast, types, sys, six, io

    if isinstance(verbose, six.string_types):
        try:
            # Try to interpret string as boolean or integer values
            verbose = ast.literal_eval(verbose.title())

        except:
            # Try to open file from given string
            try:
                verbose = open(verbose, openmode)
            except:
                pass

    if isinstance(verbose, (int, bool)):
        return sys.stdout if verbose else None

    if verbose is None \
            or ((sys.version_info[0] >= 3 and isinstance(verbose, io.IOBase))
                or (sys.version_info[0] < 3
                    and isinstance(verbose, types.FileType))):
        return verbose

    return None
