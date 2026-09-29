from sports_szu.desktop import main

if __name__ == '__main__':
    import sys
    if '--smoke-test' in sys.argv:
        from sports_szu.smoke import smoke
        raise SystemExit(smoke())
    main()
